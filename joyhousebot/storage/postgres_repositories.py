"""Cohesive PostgreSQL repository groups sharing one transactional backend."""

from __future__ import annotations

from typing import Any

from joyhousebot.storage.contracts import RuntimeStores
from joyhousebot.storage.postgres_admins import PostgresAdminStoreMixin
from joyhousebot.storage.postgres_agent_skills import PostgresAgentSkillStoreMixin
from joyhousebot.storage.postgres_agent_teams import PostgresAgentTeamStoreMixin
from joyhousebot.storage.postgres_agents import PostgresAgentStoreMixin
from joyhousebot.storage.postgres_app_callbacks import PostgresAppCallbackStoreMixin
from joyhousebot.storage.postgres_app_delegation import PostgresAppDelegationStoreMixin
from joyhousebot.storage.postgres_app_usage import PostgresAppUsageStoreMixin
from joyhousebot.storage.postgres_approvals import PostgresApprovalStoreMixin
from joyhousebot.storage.postgres_apps import PostgresAppStoreMixin
from joyhousebot.storage.postgres_artifact_uploads import PostgresArtifactUploadStoreMixin
from joyhousebot.storage.postgres_cancel import PostgresRunCancelMixin
from joyhousebot.storage.postgres_capabilities import PostgresCapabilityStoreMixin
from joyhousebot.storage.postgres_clarifications import PostgresClarificationStoreMixin
from joyhousebot.storage.postgres_context_manifests import PostgresContextManifestStoreMixin
from joyhousebot.storage.postgres_device_deliveries import PostgresDeviceDeliveryStoreMixin
from joyhousebot.storage.postgres_device_host_controls import PostgresDeviceHostControlStoreMixin
from joyhousebot.storage.postgres_device_hosts import PostgresDeviceHostStoreMixin
from joyhousebot.storage.postgres_embedding_profiles import PostgresEmbeddingProfileStoreMixin
from joyhousebot.storage.postgres_evals import PostgresEvalStoreMixin
from joyhousebot.storage.postgres_event_triggers import PostgresEventTriggerStoreMixin
from joyhousebot.storage.postgres_execution_loop import PostgresExecutionLoopStoreMixin
from joyhousebot.storage.postgres_experiments import PostgresExperimentStoreMixin
from joyhousebot.storage.postgres_extension_inventory import PostgresExtensionInventoryStoreMixin
from joyhousebot.storage.postgres_extensions import PostgresExtensionStoreMixin
from joyhousebot.storage.postgres_graph_actions import PostgresGraphActionStoreMixin
from joyhousebot.storage.postgres_graph_branches import PostgresGraphBranchStoreMixin
from joyhousebot.storage.postgres_graph_control_nodes import PostgresGraphControlNodeStoreMixin
from joyhousebot.storage.postgres_graph_foreach import PostgresGraphForeachStoreMixin
from joyhousebot.storage.postgres_graph_loops import PostgresGraphLoopStoreMixin
from joyhousebot.storage.postgres_graph_patches import PostgresGraphPatchStoreMixin
from joyhousebot.storage.postgres_graph_revisions import PostgresGraphRevisionStoreMixin
from joyhousebot.storage.postgres_graph_sagas import PostgresGraphSagaStoreMixin
from joyhousebot.storage.postgres_graph_subruns import PostgresGraphSubrunStoreMixin
from joyhousebot.storage.postgres_graph_wait_events import PostgresGraphWaitEventStoreMixin
from joyhousebot.storage.postgres_graphs import PostgresGraphStoreMixin
from joyhousebot.storage.postgres_host_tools import PostgresHostToolStoreMixin
from joyhousebot.storage.postgres_input_assets import PostgresInputAssetStoreMixin
from joyhousebot.storage.postgres_loop_decisions import PostgresLoopDecisionStoreMixin
from joyhousebot.storage.postgres_memory_candidates import PostgresMemoryCandidateStoreMixin
from joyhousebot.storage.postgres_migrations import PostgresMigrationMixin
from joyhousebot.storage.postgres_model_gateway import PostgresModelGatewayStoreMixin
from joyhousebot.storage.postgres_model_providers import PostgresModelProviderStoreMixin
from joyhousebot.storage.postgres_observability import PostgresObservabilityStoreMixin
from joyhousebot.storage.postgres_operational_metrics import PostgresOperationalMetricsStoreMixin
from joyhousebot.storage.postgres_operations import PostgresOperationsStoreMixin
from joyhousebot.storage.postgres_owner_delegation import PostgresOwnerDelegationStoreMixin
from joyhousebot.storage.postgres_plan_confirmations import PostgresPlanConfirmationStoreMixin
from joyhousebot.storage.postgres_prompts import PostgresPromptStoreMixin
from joyhousebot.storage.postgres_rate_limits import PostgresRateLimitStoreMixin
from joyhousebot.storage.postgres_reconciliations import PostgresReconciliationStoreMixin
from joyhousebot.storage.postgres_remote_connections import PostgresRemoteConnectionStoreMixin
from joyhousebot.storage.postgres_rollouts import PostgresRolloutStoreMixin
from joyhousebot.storage.postgres_run_listing import PostgresRunListingStoreMixin
from joyhousebot.storage.postgres_runs import PostgresRunStoreMixin
from joyhousebot.storage.postgres_scenarios import PostgresScenarioStoreMixin
from joyhousebot.storage.postgres_skills import PostgresSkillStoreMixin
from joyhousebot.storage.postgres_tasks import PostgresTaskStoreMixin
from joyhousebot.storage.postgres_verifications import PostgresVerificationStoreMixin
from joyhousebot.storage.postgres_workflows import PostgresWorkflowStoreMixin
from joyhousebot.storage.postgres_works import PostgresWorkStoreMixin


class _RepositoryGroup:
    """Forward shared state and cross-domain calls to the composition root."""

    def __init__(self, backend: Any) -> None:
        object.__setattr__(self, "_backend", backend)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_backend"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_backend":
            object.__setattr__(self, name, value)
            return
        setattr(object.__getattribute__(self, "_backend"), name, value)


class _PortView:
    """Narrow method view over the repository groups needed by one service port."""

    def __init__(self, *repositories: _RepositoryGroup) -> None:
        self._repositories = repositories

    def __getattr__(self, name: str) -> Any:
        for repository in self._repositories:
            for base in type(repository).__mro__:
                if name in vars(base):
                    return getattr(repository, name)
        raise AttributeError(name)


class PostgresOperationsRepository(
    _RepositoryGroup,
    PostgresMigrationMixin,
    PostgresRateLimitStoreMixin,
    PostgresOperationalMetricsStoreMixin,
    PostgresOperationsStoreMixin,
):
    """Schema, health, maintenance, pool metrics, and operational controls."""


class PostgresRuntimeRepository(
    _RepositoryGroup,
    PostgresExecutionLoopStoreMixin,
    PostgresContextManifestStoreMixin,
    PostgresRunListingStoreMixin,
    PostgresRunStoreMixin,
    PostgresRunCancelMixin,
    PostgresTaskStoreMixin,
    PostgresObservabilityStoreMixin,
):
    """Run, Task, turn journal, context, trace, and execution state."""


class PostgresGraphRepository(
    _RepositoryGroup,
    PostgresGraphRevisionStoreMixin,
    PostgresGraphSagaStoreMixin,
    PostgresGraphSubrunStoreMixin,
    PostgresGraphPatchStoreMixin,
    PostgresGraphWaitEventStoreMixin,
    PostgresGraphStoreMixin,
    PostgresGraphBranchStoreMixin,
    PostgresGraphForeachStoreMixin,
    PostgresGraphLoopStoreMixin,
    PostgresGraphControlNodeStoreMixin,
    PostgresGraphActionStoreMixin,
):
    """Graph revisions, nodes, patches, Saga, branches, loops, and waits."""


class PostgresCatalogRepository(
    _RepositoryGroup,
    PostgresAgentTeamStoreMixin,
    PostgresPlanConfirmationStoreMixin,
    PostgresWorkflowStoreMixin,
    PostgresEvalStoreMixin,
    PostgresExperimentStoreMixin,
    PostgresPromptStoreMixin,
    PostgresAgentSkillStoreMixin,
    PostgresAgentStoreMixin,
    PostgresSkillStoreMixin,
    PostgresCapabilityStoreMixin,
    PostgresLoopDecisionStoreMixin,
    PostgresVerificationStoreMixin,
    PostgresScenarioStoreMixin,
):
    """Versioned Agent, Team, Skill, Capability, Scenario, Workflow, and Eval catalog."""


class PostgresGovernanceRepository(
    _RepositoryGroup,
    PostgresApprovalStoreMixin,
    PostgresReconciliationStoreMixin,
    PostgresClarificationStoreMixin,
    PostgresEventTriggerStoreMixin,
):
    """Approval, reconciliation, clarification, and external event state."""


class PostgresAssetRepository(
    _RepositoryGroup,
    PostgresAppCallbackStoreMixin,
    PostgresAppDelegationStoreMixin,
    PostgresAppStoreMixin,
    PostgresAppUsageStoreMixin,
    PostgresWorkStoreMixin,
    PostgresArtifactUploadStoreMixin,
    PostgresInputAssetStoreMixin,
):
    """Apps, Work, immutable Artifacts, uploads, and input assets."""


class PostgresDeviceRepository(
    _RepositoryGroup,
    PostgresDeviceHostControlStoreMixin,
    PostgresDeviceHostStoreMixin,
    PostgresDeviceDeliveryStoreMixin,
    PostgresHostToolStoreMixin,
    PostgresModelGatewayStoreMixin,
):
    """Device Hosts, protected host tools, deliveries, and model grants."""


class PostgresConfigurationRepository(
    _RepositoryGroup,
    PostgresAdminStoreMixin,
    PostgresOwnerDelegationStoreMixin,
    PostgresMemoryCandidateStoreMixin,
    PostgresRolloutStoreMixin,
    PostgresModelProviderStoreMixin,
    PostgresEmbeddingProfileStoreMixin,
    PostgresRemoteConnectionStoreMixin,
    PostgresExtensionInventoryStoreMixin,
    PostgresExtensionStoreMixin,
):
    """Admin security and versioned deployment configuration."""


class PostgresRepositorySet:
    """Resolve public repository methods across explicit cohesive components."""

    def __init__(self, backend: Any) -> None:
        self.operations = PostgresOperationsRepository(backend)
        self.runtime = PostgresRuntimeRepository(backend)
        self.graphs = PostgresGraphRepository(backend)
        self.catalog = PostgresCatalogRepository(backend)
        self.governance = PostgresGovernanceRepository(backend)
        self.assets = PostgresAssetRepository(backend)
        self.devices = PostgresDeviceRepository(backend)
        self.configuration = PostgresConfigurationRepository(backend)
        self._groups = (
            self.operations,
            self.runtime,
            self.graphs,
            self.catalog,
            self.governance,
            self.assets,
            self.devices,
            self.configuration,
        )
        self._owners = self._index_method_owners()

    def _index_method_owners(self) -> dict[str, Any]:
        owners: dict[str, Any] = {}
        for group in self._groups:
            for base in type(group).__mro__:
                if base in {_RepositoryGroup, object}:
                    continue
                for name, value in vars(base).items():
                    if name.startswith("__") or not callable(value):
                        continue
                    owners.setdefault(name, group)
        return owners

    def resolve(self, name: str) -> Any:
        owner = self._owners.get(name)
        if owner is None:
            raise AttributeError(name)
        return getattr(owner, name)

    def runtime_stores(self) -> RuntimeStores:
        """Build typed Runtime ports without exposing the control-plane facade."""
        return RuntimeStores(
            runs=_PortView(self.runtime, self.governance),
            tasks=_PortView(self.runtime, self.operations),
            events=_PortView(self.runtime),
            graphs=_PortView(self.graphs, self.runtime),
            workers=_PortView(self.operations),
            catalog=_PortView(self.catalog),
            logs=_PortView(self.operations),
            traces=_PortView(self.operations),
            scenarios=_PortView(self.catalog, self.governance),
            maintenance=_PortView(
                self.operations, self.governance, self.catalog, self.configuration
            ),
            execution=_PortView(
                self.runtime, self.catalog, self.operations, self.assets
            ),
            observability=_PortView(self.runtime),
            reconciliations=_PortView(self.governance, self.graphs, self.runtime),
            experiments=_PortView(self.catalog),
            team_workspace=_PortView(self.catalog),
            planning=_PortView(self.catalog, self.runtime),
            plan_confirmations=_PortView(self.catalog),
            clarifications=_PortView(
                self.governance, self.catalog, self.operations
            ),
            invocations=_PortView(self.catalog),
            workflows=_PortView(self.catalog),
            graph_patches=_PortView(self.graphs),
            app_dependencies=_PortView(self.configuration, self.catalog),
        )
