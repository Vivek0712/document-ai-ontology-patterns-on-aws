/**
 * Amazon States Language for `rp-ingest`, built as plain JSON so the state names,
 * Parameters envelopes and ResultPaths match the contract exactly. Pure function of the ARNs/URLs,
 * unit-tested in test/ingest-asl.test.ts.
 *
 * Every Lambda task: Resource = function ARN (no Payload wrapper), Parameters = StageEnvelope,
 * ResultPath = $.stages.<stageId>, Catch States.ALL -> $.error -> LaneFailed.
 */

export interface IngestAslInputs {
  fn: {
    register: string;
    textLayer: string;
    naiveChunk: string;
    route: string;
    stageStatus: string;
    textractPage: string;
    classify: string;
    euBuilder: string;
    piiGate: string;
    promote: string;
    embedIndex: string;
    graphWrite: string;
    markReady: string;
  };
  doclingQueueUrl: string;
  doclingHeartbeatSeconds?: number;
  doclingTimeoutSeconds?: number;
  textractMaxConcurrency?: number;
}

type Json = Record<string, unknown>;

export const LAMBDA_RETRY: Json = {
  ErrorEquals: [
    'Lambda.ServiceException',
    'Lambda.AWSLambdaException',
    'Lambda.SdkClientException',
    'Lambda.TooManyRequestsException',
  ],
  IntervalSeconds: 2,
  MaxAttempts: 3,
  BackoffRate: 2,
};

export const TEXTRACT_RETRY: Json = {
  ErrorEquals: ['ThrottlingException', 'ProvisionedThroughputExceededException'],
  IntervalSeconds: 2,
  MaxAttempts: 5,
  BackoffRate: 2,
  JitterStrategy: 'FULL',
};

const CATCH_TO_LANE_FAILED = [{ ErrorEquals: ['States.ALL'], ResultPath: '$.error', Next: 'LaneFailed' }];

/** StageEnvelope Parameters block shared by every stage task. */
export function envelope(extra: Json = {}): Json {
  return {
    'ctx.$': '$.ctx',
    'stages.$': '$.stages',
    'executionArn.$': '$$.Execution.Id',
    'stateName.$': '$$.State.Name',
    'attempt.$': '$$.State.RetryCount',
    ...extra,
  };
}

function stageTask(resource: string, resultPath: string, next: string, opts: {
  retry?: Json[] | null;
  parameters?: Json;
  comment?: string;
} = {}): Json {
  const task: Json = {
    Type: 'Task',
    Resource: resource,
    Parameters: opts.parameters ?? envelope(),
    ResultPath: resultPath,
    Next: next,
    Catch: CATCH_TO_LANE_FAILED,
  };
  if (opts.comment) task.Comment = opts.comment;
  if (opts.retry !== null) task.Retry = opts.retry ?? [LAMBDA_RETRY];
  return task;
}

export function buildIngestAsl(i: IngestAslInputs): Json {
  const f = i.fn;
  const states: Record<string, Json> = {
    // ---- register (both modes) ----------------------------------------------------------
    Register: {
      Type: 'Task',
      Comment: 'Validate the Bronze object, build DocCtx, write PENDING/SKIPPED stage records',
      Resource: f.register,
      Parameters: { 'trigger.$': '$', 'executionArn.$': '$$.Execution.Id' },
      ResultPath: '$',
      Retry: [LAMBDA_RETRY],
      // No ctx exists yet, so LaneFailed cannot run; rp-ingest-failed (EventBridge) is the backstop.
      Catch: [{ ErrorEquals: ['States.ALL'], ResultPath: '$.error', Next: 'RegisterFailed' }],
      Next: 'ModeChoice',
    },
    ModeChoice: {
      Type: 'Choice',
      Choices: [
        { Variable: '$.stages.register.output.validation', StringEquals: 'skipped', Next: 'Done' },
        { Variable: '$.stages.register.output.validation', StringEquals: 'quarantined', Next: 'Rejected' },
        { Variable: '$.ctx.mode', StringEquals: 'naive', Next: 'TextLayer' },
        { Variable: '$.ctx.mode', StringEquals: 'evidence_units', Next: 'Route' },
      ],
      Default: 'UnknownMode',
    },

    // ---- 1.0 naive lane ------------------------------------------------------------------------
    TextLayer: stageTask(f.textLayer, '$.stages.extract', 'NaiveChunk'),
    NaiveChunk: stageTask(f.naiveChunk, '$.stages.units', 'PiiGate'),

    // ---- 2.3.1 evidence-unit lane ------------------------------------------------------------------
    Route: stageTask(f.route, '$.stages.route', 'ExtractOpen'),
    ExtractOpen: stageTask(f.stageStatus, '$.stages.extractOpen', 'Extract', {
      parameters: envelope({ op: 'start', stageId: 'extract' }),
    }),
    Extract: {
      Type: 'Parallel',
      Comment: 'Digital pages -> docling-serve on Fargate via SQS task token; scanned pages -> Textract Map',
      ResultPath: '$.extractParts',
      Next: 'ExtractClose',
      Catch: CATCH_TO_LANE_FAILED,
      Branches: [
        {
          StartAt: 'HasDigital',
          States: {
            HasDigital: {
              Type: 'Choice',
              Choices: [{ Variable: '$.stages.route.output.digitalPages[0]', IsPresent: true, Next: 'DoclingExtract' }],
              Default: 'NoDigitalPages',
            },
            NoDigitalPages: { Type: 'Pass', Result: {}, End: true },
            DoclingExtract: {
              Type: 'Task',
              Resource: 'arn:aws:states:::sqs:sendMessage.waitForTaskToken',
              Parameters: {
                QueueUrl: i.doclingQueueUrl,
                MessageBody: {
                  'taskToken.$': '$$.Task.Token',
                  'ctx.$': '$.ctx',
                  'pages.$': '$.stages.route.output.digitalPages',
                  'executionArn.$': '$$.Execution.Id',
                },
              },
              HeartbeatSeconds: i.doclingHeartbeatSeconds ?? 600,
              TimeoutSeconds: i.doclingTimeoutSeconds ?? 2700, // cold start + resumed conversion (docling_call)
              End: true,
            },
          },
        },
        {
          StartAt: 'HasScan',
          States: {
            HasScan: {
              Type: 'Choice',
              Choices: [{ Variable: '$.stages.route.output.scanPages[0]', IsPresent: true, Next: 'TextractPages' }],
              Default: 'NoScanPages',
            },
            NoScanPages: { Type: 'Pass', Result: [], End: true },
            TextractPages: {
              Type: 'Map',
              ItemsPath: '$.stages.route.output.scanPages',
              ItemSelector: { 'ctx.$': '$.ctx', 'page.$': '$$.Map.Item.Value', 'executionArn.$': '$$.Execution.Id' },
              MaxConcurrency: i.textractMaxConcurrency ?? 4,
              ItemProcessor: {
                ProcessorConfig: { Mode: 'INLINE' },
                StartAt: 'TextractPage',
                States: {
                  TextractPage: {
                    Type: 'Task',
                    Resource: f.textractPage,
                    Retry: [LAMBDA_RETRY, TEXTRACT_RETRY],
                    End: true,
                  },
                },
              },
              End: true,
            },
          },
        },
      ],
    },
    ExtractClose: stageTask(f.stageStatus, '$.stages.extract', 'Classify', {
      parameters: envelope({ op: 'close_extract', 'extractParts.$': '$.extractParts' }),
    }),
    Classify: stageTask(f.classify, '$.stages.classify', 'BuildUnits'),
    BuildUnits: stageTask(f.euBuilder, '$.stages.units', 'PiiGate'),

    // ---- shared tail -------------------------------------------------------------------------------
    PiiGate: stageTask(f.piiGate, '$.stages.pii_gate', 'Promote'),
    Promote: stageTask(f.promote, '$.stages.promote', 'IndexAndGraph'),
    IndexAndGraph: {
      Type: 'Parallel',
      ResultPath: '$.indexAndGraph',
      ResultSelector: { 'index.$': '$[0]', 'graph.$': '$[1]' },
      Next: 'MergeIndexGraph',
      Catch: CATCH_TO_LANE_FAILED,
      Branches: [
        {
          StartAt: 'EmbedIndex',
          States: {
            // No Retry: index writes use auto _ids, so a retry could duplicate documents.
            EmbedIndex: { Type: 'Task', Resource: f.embedIndex, Parameters: envelope(), End: true },
          },
        },
        {
          StartAt: 'GraphChoice',
          States: {
            GraphChoice: {
              Type: 'Choice',
              Choices: [{ Variable: '$.ctx.graphEnabled', BooleanEquals: true, Next: 'GraphWrite' }],
              Default: 'GraphSkipped',
            },
            GraphWrite: {
              Type: 'Task', Resource: f.graphWrite, Parameters: envelope(), Retry: [LAMBDA_RETRY], End: true,
            },
            GraphSkipped: { Type: 'Pass', Result: { stageId: 'graph', state: 'SKIPPED' }, End: true },
          },
        },
      ],
    },
    MergeIndexGraph: {
      Type: 'Pass',
      Comment: 'Copy [0] -> $.stages.index and [1] -> $.stages.graph',
      Parameters: { 'ctx.$': '$.ctx', 'stages.$': 'States.JsonMerge($.stages, $.indexAndGraph, false)' },
      ResultPath: '$',
      Next: 'MarkReady',
    },
    MarkReady: stageTask(f.markReady, '$.stages.ready', 'Done'),
    Done: { Type: 'Succeed' },

    // ---- failure paths --------------------------------------------------------------------------------
    LaneFailed: {
      Type: 'Task',
      Comment: 'Fail the open stage and the lane in DynamoDB, then fail the execution',
      Resource: f.stageStatus,
      Parameters: {
        op: 'lane_failed',
        'ctx.$': '$.ctx',
        'stages.$': '$.stages',
        'error.$': '$.error',
        'executionArn.$': '$$.Execution.Id',
        'stateName.$': '$$.State.Name',
      },
      ResultPath: null,
      Retry: [LAMBDA_RETRY],
      Catch: [{ ErrorEquals: ['States.ALL'], ResultPath: null, Next: 'Failed' }],
      Next: 'Failed',
    },
    Failed: { Type: 'Fail', Error: 'LaneFailed', Cause: 'A stage failed; see the stage record in rp-doc-status' },
    RegisterFailed: { Type: 'Fail', Error: 'RegisterFailed', Cause: 'rp-register failed before a DocCtx existed' },
    Rejected: { Type: 'Fail', Error: 'Rejected', Cause: 'Upload rejected by register and moved to quarantine/' },
    UnknownMode: { Type: 'Fail', Error: 'UnknownMode', Cause: 'ctx.mode is neither naive nor evidence_units' },
  };

  return {
    Comment: 'Regionproof ingest: one execution per (object, pipelineVersion).',
    StartAt: 'Register',
    TimeoutSeconds: 3600,
    States: states,
  };
}

/** Every state name reachable in the definition (including nested branches), for tests. */
export function allStateNames(asl: Json): string[] {
  const out: string[] = [];
  const walk = (states: Record<string, Json>): void => {
    for (const [name, st] of Object.entries(states)) {
      out.push(name);
      for (const b of (st.Branches as { States: Record<string, Json> }[] | undefined) ?? []) walk(b.States);
      const proc = st.ItemProcessor as { States: Record<string, Json> } | undefined;
      if (proc) walk(proc.States);
    }
  };
  walk(asl.States as Record<string, Json>);
  return out;
}

/** The ASL with placeholder ARNs (`...:function:rp-<name>`), exported to contracts/generated/ingest_asl.json. */
export function placeholderAsl(): Json {
  const fn = (n: string): string => `arn:aws:lambda:us-east-1:000000000000:function:rp-${n}`;
  return buildIngestAsl({
    fn: {
      register: fn('register'), textLayer: fn('text-layer'), naiveChunk: fn('naive-chunk'), route: fn('route'),
      stageStatus: fn('stage-status'), textractPage: fn('textract-page'), classify: fn('classify'),
      euBuilder: fn('eu-builder'), piiGate: fn('pii-gate'), promote: fn('promote'), embedIndex: fn('embed-index'),
      graphWrite: fn('graph-write'), markReady: fn('mark-ready'),
    },
    doclingQueueUrl: 'https://sqs.us-east-1.amazonaws.com/000000000000/rp-docling',
  });
}
