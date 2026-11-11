"""COA environment lifecycle: spin COA up and down from Settings → COA environment (decision record 0006).

- ``CoaControlConfig`` is ``config/coa.yaml`` (plus the ``coaEnvironment`` block of ``config/prices.yaml``).
- ``CoaEnvItem`` is the one row of table ``rp-coa-env`` (pk ``env``), written only by ``rp-coa-control`` and always
  conditionally on its ``version`` (optimistic locking).
- ``CoaEnvironmentView`` is what ``GET /coa/environment`` returns; the action routes return it too, with ``outcome``.

Times are ISO-8601 UTC strings with a ``Z``. Cost figures are list-price estimates (``provenance: estimate``), never
billed amounts, and only an environment admin's view carries them.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ..base import RpModel

CoaEnvState = Literal["off", "starting", "running", "stopping", "failed", "partially_down"]
CoaOperation = Literal["up", "down"]
CoaPhaseId = Literal["queued", "provisioning", "install", "pre_build", "build", "post_build", "done"]
CoaPhaseStatus = Literal["pending", "running", "succeeded", "failed", "skipped"]
CoaBuildResult = Literal["ok", "eni_pending", "leftovers", "failed"]
CoaActivitySourceId = Literal["api_gateway", "agentcore", "provenant", "keep_alive", "became_running"]
CoaOutcome = Literal["started", "noop", "updated", "stopped_and_started"]

ADMIN_GROUP = "rp-admin"
COA_ENV_TABLE: dict[str, Any] = {"name": "rp-coa-env", "pk": "env", "sk": None, "gsis": [], "ttl": None,
                                 "stream": False}
COA_ENV_TABLE_ENV = "RP_TABLE_COA_ENV"
COA_CONTROL_FUNCTION = "rp-coa-control"
CONFIG_KEY = "config/coa.yaml"


# --- configuration (config/coa.yaml) ------------------------------------------------------------------------

class CoaRepoConfig(RpModel):
    repo: str
    tag: str
    commit: str  # the exact commit of ``tag``; the build refuses any other
    prefix: str
    env: str
    region: str
    deploy_context: dict[str, str] = Field(default_factory=dict)
    smus_admin_arns_parameter: str
    config_parameter: str

    @property
    def stack_prefix(self) -> str:
        return f"{self.prefix}-{self.env}"


class CoaBootstrapConfig(RpModel):
    """COA's own CDK bootstrap (decision 0006): the qualifier, and the two guard policies around it."""

    qualifier: str
    toolkit_stack_name: str
    boundary_policy: str
    deploy_guard_policy: str


class CoaChecksums(RpModel):
    node: str
    maven: str
    uv: str
    pnpm: str


class CoaToolchain(RpModel):
    java: str
    node: str
    pnpm: str
    maven: str
    uv: str
    python: str


class CoaExecutorConfig(RpModel):
    project: str
    image: str
    compute_type: str
    timeout_minutes: int
    concurrency: int
    binfmt_image: str
    toolchain: CoaToolchain
    checksums: CoaChecksums


class CoaLifecycleConfig(RpModel):
    up_minutes: tuple[int, int]
    down_minutes: tuple[int, int]
    eni_retry_minutes: int
    eni_max_retries: int
    eni_window_hours: int
    failed_down_retry_minutes: int = 30
    failed_down_auto_retries: int = 1
    settle_minutes: int = 60


class CoaApiGatewayActivity(RpModel):
    namespace: str
    metric: str
    error_metric: str = "4XXError"
    api_name: str
    min_requests_per_period: int = 1


class CoaAgentcoreActivity(RpModel):
    namespace: str
    metric: str
    name_prefix: str
    recently_active: str = "PT3H"


class CoaActivityConfig(RpModel):
    api_gateway: CoaApiGatewayActivity
    agentcore: CoaAgentcoreActivity


class CoaIdleConfig(RpModel):
    default_auto_teardown: bool
    default_minutes: int
    min_minutes: int
    max_minutes: int
    tick_minutes: int
    keep_alive_minutes: int
    cooldown_minutes: int
    max_session_hours: int = 10
    probe_minutes: int = 15
    activity: CoaActivityConfig


class CoaOutputRef(RpModel):
    stack: str
    output_prefix: str


class CoaOutputsConfig(RpModel):
    console_url: CoaOutputRef
    rest_endpoint: CoaOutputRef
    probe_stack: str


class CoaSeedNamespace(RpModel):
    name: str
    display_name: str
    description: str


class CoaSeedCheck(RpModel):
    package_id: str
    expected: str


class CoaSeedConfig(RpModel):
    namespace: CoaSeedNamespace
    seed_user: str
    bucket_prefix: str
    glue_database: str
    glue_table: str
    csv: str
    ontology: str
    ontology_id: str
    metric: str
    check: CoaSeedCheck


class CoaCostComponent(RpModel):
    id: str
    label: str
    usd_per_hour: float
    source: str


class CoaPriceConfig(RpModel):
    as_of: str
    components: list[CoaCostComponent]

    @property
    def usd_per_hour(self) -> float:
        return round(sum(c.usd_per_hour for c in self.components), 4)


class CoaControlConfig(RpModel):
    coa: CoaRepoConfig
    bootstrap: CoaBootstrapConfig
    executor: CoaExecutorConfig
    lifecycle: CoaLifecycleConfig
    idle: CoaIdleConfig
    outputs: CoaOutputsConfig
    seed: CoaSeedConfig
    prices: CoaPriceConfig | None = None  # from config/prices.yaml coaEnvironment

    def clamp_idle(self, minutes: int) -> int:
        return max(self.idle.min_minutes, min(self.idle.max_minutes, int(minutes)))


def parse_config(coa_yaml: str, prices_yaml: str | None = None) -> CoaControlConfig:
    import yaml

    raw = yaml.safe_load(coa_yaml) or {}
    if prices_yaml:
        block = (yaml.safe_load(prices_yaml) or {}).get("coaEnvironment")
        if block:
            raw["prices"] = block
    return CoaControlConfig.model_validate(raw)


def cli_commands() -> list[str]:
    return ["make coa-status", "make coa-up", "make coa-down"]


# --- state (table rp-coa-env) ------------------------------------------------------------------------------

class CoaPhase(RpModel):
    id: CoaPhaseId
    label: str
    status: CoaPhaseStatus = "pending"
    started_at: str | None = None
    ended_at: str | None = None


class CoaBuild(RpModel):
    """One CodeBuild run of ``rp-coa-lifecycle`` (the executor)."""

    id: str
    operation: CoaOperation
    status: str = "IN_PROGRESS"  # CodeBuild buildStatus
    started_at: str
    ended_at: str | None = None
    reason: str
    requested_by: str  # caller sub (6 characters) or "idle-watcher"
    attempt: int = 1  # destroy retries while ENIs are pending
    phases: list[CoaPhase] = Field(default_factory=list)
    log_url: str | None = None
    result: CoaBuildResult | None = None


class CoaSettings(RpModel):
    auto_teardown: bool
    idle_minutes: int
    min_minutes: int
    max_minutes: int
    keep_alive_minutes: int
    max_session_hours: int | None = None


class CoaActivity(RpModel):
    source: CoaActivitySourceId
    at: str | None = None
    detail: str | None = None


class CoaError(RpModel):
    message: str
    at: str
    phase: str | None = None
    log_url: str | None = None


class CoaEndpoints(RpModel):
    console_url: str | None = None
    rest_endpoint: str | None = None
    read_at: str | None = None


class CoaEniRetry(RpModel):
    retries: int
    max_retries: int
    next_retry_at: str | None = None
    window_ends_at: str | None = None


class CoaHistoryEntry(RpModel):
    at: str
    from_state: CoaEnvState
    to_state: CoaEnvState
    reason: str
    by: str


# Written by the background writers (idle watcher, Provenant touch) with UpdateItem and no version bump; a versioned
# write of the state machine never overwrites them (``service.Store.put`` leaves these attributes alone).
BACKGROUND_FIELDS = ("seen", "seen_count", "checked_at", "activity_error", "activity_error_since", "probed_at")


class CoaEnvItem(RpModel):
    env: str  # partition key: "<prefix>-<env>" (coa-dev)
    version: int = 0
    state: CoaEnvState = "off"
    state_since: str
    failed_operation: CoaOperation | None = None  # in failed: which operation failed (decides what may follow)
    running_since: str | None = None  # this session: set when the environment became running
    session_started_at: str | None = None  # when the spin-up of this session began (cost clock)
    stopped_at: str | None = None
    reason: str | None = None
    build: CoaBuild | None = None
    auto_teardown: bool = True
    idle_minutes: int = 60
    activity: dict[str, str] = Field(default_factory=dict)  # state-machine sources: became_running, keep_alive
    seen: dict[str, str] = Field(default_factory=dict)  # background sources: api_gateway, agentcore, provenant
    seen_count: dict[str, float] = Field(default_factory=dict)  # requests / invocations behind each ``seen`` time
    keep_alive_until: str | None = None
    checked_at: str | None = None  # last idle-watcher tick
    activity_error: str | None = None  # CloudWatch could not be read (since ``activity_error_since``)
    activity_error_since: str | None = None
    probed_at: str | None = None  # last CloudFormation check while off (COA deployed outside this control)
    down_auto_retries: int = 0  # automatic retries of a failed tear-down this session
    notice: str | None = None  # shown until an admin acts (e.g. COA adopted with auto tear-down off)
    eni_retries: int = 0
    eni_first_at: str | None = None
    next_retry_at: str | None = None
    error: CoaError | None = None
    endpoints: CoaEndpoints | None = None
    history: list[CoaHistoryEntry] = Field(default_factory=list)
    last_idempotency_key: str | None = None
    detected: bool = False  # the first state came from CloudFormation, not from an operation here


# --- API ---------------------------------------------------------------------------------------------------

class CoaIdleView(RpModel):
    last_activity_at: str | None = None
    last_activity_source: CoaActivitySourceId | None = None
    sources: list[CoaActivity] = Field(default_factory=list)
    keep_alive_until: str | None = None
    teardown_at: str | None = None  # when auto tear-down starts if nothing happens (auto tear-down on)
    cooldown_until: str | None = None
    checked_at: str | None = None
    max_session_until: str | None = None  # the hard cap (idle.maxSessionHours), unless kept alive
    activity_error: str | None = None
    activity_error_since: str | None = None


class CoaCostEstimate(RpModel):
    provenance: Literal["estimate"] = "estimate"
    usd_per_hour: float
    session_hours: float | None = None
    session_usd: float | None = None
    components: list[CoaCostComponent]
    as_of: str
    source: str = "config/prices.yaml coaEnvironment"
    note: str = "List-price estimate while COA is up; not a billed amount."
    residual: str | None = None  # what may still bill after the session clock stopped


class CoaViewer(RpModel):
    can_operate: bool
    admin_group: str = ADMIN_GROUP


class CoaLifecycleInfo(RpModel):
    up_minutes: tuple[int, int]
    down_minutes: tuple[int, int]
    eni_window_hours: int
    coa_tag: str
    stack_prefix: str
    project: str


class CoaEnvironmentView(RpModel):
    deployed: bool  # the rp-coa-control stack exists (context rp:coaControl)
    mode: Literal["live", "simulated"] = "live"
    server_time: str | None = None  # the clock the deadlines are computed against
    state: CoaEnvState | None = None
    failed_operation: CoaOperation | None = None
    version: int = 0
    state_since: str | None = None
    running_since: str | None = None
    session_started_at: str | None = None
    stopped_at: str | None = None
    reason: str | None = None
    build: CoaBuild | None = None
    settings: CoaSettings | None = None
    idle: CoaIdleView | None = None
    cost: CoaCostEstimate | None = None
    endpoints: CoaEndpoints | None = None
    error: CoaError | None = None
    eni: CoaEniRetry | None = None
    history: list[CoaHistoryEntry] = Field(default_factory=list)
    viewer: CoaViewer
    lifecycle: CoaLifecycleInfo | None = None
    cli: list[str] = Field(default_factory=cli_commands)
    detected: bool = False
    outcome: CoaOutcome | None = None  # action routes only
    note: str | None = None
    notice: str | None = None


class CoaUpRequest(RpModel):
    expected_version: int | None = None
    idempotency_key: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")


class CoaDownRequest(CoaUpRequest):
    force: bool = False  # admin only: stop a running spin-up and tear down


class CoaSettingsRequest(RpModel):
    auto_teardown: bool
    idle_minutes: int
    expected_version: int | None = None


class CoaKeepAliveRequest(RpModel):
    expected_version: int | None = None
