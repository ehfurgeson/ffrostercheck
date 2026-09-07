"""Game-day job planning and execution."""

from app.scheduling.final import (
    FinalExecution,
    FinalExecutionError,
    FinalLineupSnapshot,
    execute_final_job,
    execute_official_final_job,
    render_final_execution,
)
from app.scheduling.planner import (
    GameDayPlan,
    PlannedJob,
    PlannedJobKind,
    build_game_day_plan,
    render_game_day_plan,
)
from app.scheduling.prefetch import (
    PrefetchExecution,
    PrefetchExecutionError,
    PrefetchGameResult,
    execute_official_prefetch_job,
    execute_prefetch_job,
    render_prefetch_execution,
)
from app.scheduling.production import (
    OperationalSnapshot,
    ProductionServiceError,
    load_operational_snapshot,
    run_production_game_day,
)
from app.scheduling.service import (
    GameDayServiceResult,
    ServiceJobResult,
    render_game_day_service,
    run_game_day_service,
)

__all__ = [
    "GameDayPlan",
    "GameDayServiceResult",
    "FinalExecution",
    "FinalExecutionError",
    "FinalLineupSnapshot",
    "OperationalSnapshot",
    "PlannedJob",
    "PlannedJobKind",
    "PrefetchExecution",
    "PrefetchExecutionError",
    "PrefetchGameResult",
    "ProductionServiceError",
    "ServiceJobResult",
    "build_game_day_plan",
    "execute_official_prefetch_job",
    "execute_final_job",
    "execute_official_final_job",
    "execute_prefetch_job",
    "load_operational_snapshot",
    "render_game_day_plan",
    "render_game_day_service",
    "render_final_execution",
    "render_prefetch_execution",
    "run_game_day_service",
    "run_production_game_day",
]
