import { LAMBDA_RETRY, allStateNames, buildIngestAsl, placeholderAsl } from '../lib/ingest-asl';

type State = Record<string, any>;

const fn = (n: string): string => `arn:aws:lambda:us-east-1:000000000000:function:rp-${n}`;
const asl = buildIngestAsl({
  fn: {
    register: fn('register'), textLayer: fn('text-layer'), naiveChunk: fn('naive-chunk'), route: fn('route'),
    stageStatus: fn('stage-status'), textractPage: fn('textract-page'), classify: fn('classify'),
    euBuilder: fn('eu-builder'), piiGate: fn('pii-gate'), promote: fn('promote'), embedIndex: fn('embed-index'),
    graphWrite: fn('graph-write'), markReady: fn('mark-ready'),
  },
  doclingQueueUrl: 'https://sqs.us-east-1.amazonaws.com/000000000000/rp-docling',
});
const states = asl.States as Record<string, State>;
const branch = (parallel: string, i: number): Record<string, State> => states[parallel].Branches[i].States;

describe('rp-ingest ASL', () => {
  test('contract state names exist', () => {
    const names = allStateNames(asl);
    for (const s of ['Register', 'ModeChoice', 'TextLayer', 'NaiveChunk', 'Route', 'ExtractOpen', 'Extract',
      'DoclingExtract', 'TextractPages', 'TextractPage', 'ExtractClose', 'Classify', 'BuildUnits', 'PiiGate',
      'Promote', 'IndexAndGraph', 'EmbedIndex', 'GraphChoice', 'GraphWrite', 'GraphSkipped', 'MergeIndexGraph',
      'MarkReady', 'Done', 'LaneFailed', 'Rejected']) {
      expect(names).toContain(s);
    }
  });

  test('every transition targets an existing state', () => {
    const check = (sts: Record<string, State>): void => {
      const names = new Set(Object.keys(sts));
      for (const [name, st] of Object.entries(sts)) {
        const targets = [st.Next, st.Default, ...(st.Choices ?? []).map((c: State) => c.Next)].filter(Boolean);
        for (const t of targets) expect({ from: name, to: names.has(t) ? t : `MISSING ${t}` }).toEqual({ from: name, to: t });
        for (const c of st.Catch ?? []) {
          // catches inside branches cannot leave the branch; only top-level states catch to LaneFailed
          expect(names.has(c.Next)).toBe(true);
        }
        for (const b of st.Branches ?? []) check(b.States);
        if (st.ItemProcessor) check(st.ItemProcessor.States);
      }
    };
    check(states);
  });

  test('register replaces the state and routes by validation then mode', () => {
    expect(states.Register.Parameters).toEqual({ 'trigger.$': '$', 'executionArn.$': '$$.Execution.Id' });
    expect(states.Register.ResultPath).toBe('$');
    const choices = states.ModeChoice.Choices.map((c: State) => [c.Variable, c.StringEquals, c.Next]);
    expect(choices).toEqual([
      ['$.stages.register.output.validation', 'skipped', 'Done'],
      ['$.stages.register.output.validation', 'quarantined', 'Rejected'],
      ['$.ctx.mode', 'naive', 'TextLayer'],
      ['$.ctx.mode', 'evidence_units', 'Route'],
    ]);
  });

  test('stage tasks use the envelope, ResultPath per stage and catch to LaneFailed', () => {
    const expected: Record<string, string> = {
      TextLayer: '$.stages.extract', NaiveChunk: '$.stages.units', Route: '$.stages.route',
      ExtractClose: '$.stages.extract', Classify: '$.stages.classify', BuildUnits: '$.stages.units',
      PiiGate: '$.stages.pii_gate', Promote: '$.stages.promote', MarkReady: '$.stages.ready',
    };
    for (const [name, rp] of Object.entries(expected)) {
      const st = states[name];
      expect(st.ResultPath).toBe(rp);
      expect(st.Resource).toMatch(/^arn:aws:lambda:/);
      expect(st.Parameters['ctx.$']).toBe('$.ctx');
      expect(st.Parameters['stages.$']).toBe('$.stages');
      expect(st.Parameters['executionArn.$']).toBe('$$.Execution.Id');
      expect(st.Parameters['stateName.$']).toBe('$$.State.Name');
      expect(st.Parameters['attempt.$']).toBe('$$.State.RetryCount');
      expect(st.Catch).toEqual([{ ErrorEquals: ['States.ALL'], ResultPath: '$.error', Next: 'LaneFailed' }]);
      expect(st.Retry).toEqual([LAMBDA_RETRY]);
    }
    expect(states.NaiveChunk.Next).toBe('PiiGate');
    expect(states.BuildUnits.Next).toBe('PiiGate');
  });

  test('extract: docling via SQS task token, textract via Map', () => {
    expect(states.ExtractOpen.Parameters).toMatchObject({ op: 'start', stageId: 'extract' });
    expect(states.Extract.ResultPath).toBe('$.extractParts');
    const docling = branch('Extract', 0).DoclingExtract;
    expect(docling.Resource).toBe('arn:aws:states:::sqs:sendMessage.waitForTaskToken');
    expect(docling.Parameters.MessageBody).toEqual({
      'taskToken.$': '$$.Task.Token', 'ctx.$': '$.ctx', 'pages.$': '$.stages.route.output.digitalPages',
      'executionArn.$': '$$.Execution.Id',
    });
    expect(docling.HeartbeatSeconds).toBe(600);
    expect(docling.TimeoutSeconds).toBe(2700);
    expect(branch('Extract', 0).NoDigitalPages.Result).toEqual({});
    const map = branch('Extract', 1).TextractPages;
    expect(map.ItemsPath).toBe('$.stages.route.output.scanPages');
    expect(map.ItemSelector).toEqual({ 'ctx.$': '$.ctx', 'page.$': '$$.Map.Item.Value',
      'executionArn.$': '$$.Execution.Id' });
    expect(map.MaxConcurrency).toBe(4);
    expect(map.ItemProcessor.States.TextractPage.Retry[1].MaxAttempts).toBe(5);
    expect(branch('Extract', 1).NoScanPages.Result).toEqual([]);
    expect(states.ExtractClose.Parameters).toMatchObject({ op: 'close_extract', 'extractParts.$': '$.extractParts' });
  });

  test('index has no retry; graph is skipped by config; results merge into stages', () => {
    const idx = branch('IndexAndGraph', 0).EmbedIndex;
    expect(idx.Retry).toBeUndefined();
    const graph = branch('IndexAndGraph', 1);
    expect(graph.GraphChoice.Choices[0]).toEqual({ Variable: '$.ctx.graphEnabled', BooleanEquals: true,
      Next: 'GraphWrite' });
    expect(graph.GraphSkipped.Result).toEqual({ stageId: 'graph', state: 'SKIPPED' });
    expect(states.IndexAndGraph.ResultSelector).toEqual({ 'index.$': '$[0]', 'graph.$': '$[1]' });
    expect(states.MergeIndexGraph.Parameters['stages.$']).toBe('States.JsonMerge($.stages, $.indexAndGraph, false)');
    expect(states.MergeIndexGraph.Next).toBe('MarkReady');
  });

  test('LaneFailed records the error then fails', () => {
    expect(states.LaneFailed.Parameters).toMatchObject({ op: 'lane_failed', 'ctx.$': '$.ctx', 'error.$': '$.error' });
    expect(states[states.LaneFailed.Next].Type).toBe('Fail');
    expect(states.Rejected.Type).toBe('Fail');
  });
});

describe('contracts/generated/ingest_asl.json', () => {
  test('matches lib/ingest-asl.ts (npm run export-asl)', () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const fs = require('fs');
    const file = require('path').resolve(__dirname, '..', '..', 'contracts', 'generated', 'ingest_asl.json');
    expect(JSON.parse(fs.readFileSync(file, 'utf-8'))).toEqual(placeholderAsl());
  });
});
