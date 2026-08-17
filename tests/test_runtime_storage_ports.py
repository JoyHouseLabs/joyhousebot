import inspect

from porthouse.storage.contracts import RuntimeStores
from porthouse.storage.contracts import runtime as runtime_ports
from porthouse.storage.postgres_repositories import PostgresRepositorySet
from porthouse.storage.postgres_store import PostgresRuntimeStore


def test_runtime_store_views_preserve_one_transactional_backend() -> None:
    backend = object()

    stores = RuntimeStores.from_backend(backend)

    views = (
        stores.runs,
        stores.tasks,
        stores.events,
        stores.graphs,
        stores.workers,
        stores.catalog,
        stores.logs,
        stores.traces,
        stores.scenarios,
        stores.maintenance,
        stores.execution,
        stores.observability,
        stores.reconciliations,
        stores.experiments,
        stores.team_workspace,
        stores.planning,
        stores.plan_confirmations,
        stores.clarifications,
        stores.invocations,
        stores.workflows,
        stores.graph_patches,
        stores.app_dependencies,
    )
    assert {id(value) for value in views} == {id(backend)}


def test_postgres_runtime_store_uses_explicit_repository_composition() -> None:
    backend = PostgresRuntimeStore.__new__(PostgresRuntimeStore)
    backend._repositories = PostgresRepositorySet(backend)

    stores = RuntimeStores.from_backend(backend)

    assert PostgresRuntimeStore.__bases__ == (object,)
    assert stores.runs is not backend
    assert stores.runs is not stores.graphs
    assert callable(stores.runs.get_runtime_run)
    assert callable(stores.graphs.create_runtime_graph)
    assert callable(stores.workers.register_runtime_worker)


def test_runtime_store_ports_stay_narrow() -> None:
    port_names = (
        "RunStorePort",
        "TaskStorePort",
        "EventStorePort",
        "GraphStorePort",
        "WorkerStorePort",
        "AgentCatalogStorePort",
        "RuntimeLogStorePort",
        "TraceStorePort",
        "ScenarioStorePort",
        "MaintenanceStorePort",
        "ExecutionStorePort",
        "ObservabilityStorePort",
        "ReconciliationStorePort",
        "ExperimentStorePort",
        "TeamWorkspaceStorePort",
        "PlanningStorePort",
        "PlanConfirmationStorePort",
        "ClarificationStorePort",
        "InvocationStorePort",
        "WorkflowStorePort",
        "GraphPatchStorePort",
        "AppDependencyStorePort",
    )

    for name in port_names:
        port = getattr(runtime_ports, name)
        methods = [
            member_name
            for member_name, member in inspect.getmembers(port, inspect.isfunction)
            if not member_name.startswith("_")
        ]
        assert len(methods) <= 30, f"{name} grew to {len(methods)} methods: {methods}"
