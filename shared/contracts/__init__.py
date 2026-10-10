"""Shared Pydantic contract models. Import as `from shared.contracts import ...`."""

from .models import (
    ActionResult,
    AnomalyEvent,
    AnomalyFeatures,
    ContractModel,
    DecisionProposal,
    DeploymentEvent,
    FinOpsContext,
    FinOpsRecommendation,
    Incident,
    IncidentMemory,
    IncidentStatus,
    Metrics,
    ObservedUsage,
    RCAResult,
    RecoveryMetrics,
    RecoveryResult,
    ResourceSpec,
    ScaleOption,
    TelemetrySnapshot,
)

# contracts/<file>.json  ->  the model that must accept it.
# tests/contract/test_contract_models.py fails if a file appears in
# /contracts that is not listed here, so a new or renamed contract can't
# slip in without a model.
CONTRACT_MODELS = {
    "telemetry_snapshot.json": TelemetrySnapshot,
    "deployment_event.json": DeploymentEvent,
    "anomaly_event.json": AnomalyEvent,
    "incident.json": Incident,
    "rca_result.json": RCAResult,
    "finops_context.json": FinOpsContext,
    "decision_proposal.json": DecisionProposal,
    "action_result.json": ActionResult,
    "recovery_result.json": RecoveryResult,
    "incident_memory.json": IncidentMemory,
    "finops_recommendation.json": FinOpsRecommendation,
}

# mocks/<file>.json  ->  the model that must accept it.
MOCK_MODELS = {
    "mock_metrics.json": TelemetrySnapshot,
    "mock_deployment_event.json": DeploymentEvent,
    "mock_anomaly_event.json": AnomalyEvent,
    "mock_incident.json": Incident,
    "mock_rca_response.json": RCAResult,
    "mock_finops_context.json": FinOpsContext,
    "mock_decision.json": DecisionProposal,
    "mock_action_result.json": ActionResult,
    "mock_recovery.json": RecoveryResult,
    "mock_incident_memory.json": IncidentMemory,
    "mock_finops_recommendation.json": FinOpsRecommendation,
}

__all__ = [
    "ActionResult",
    "AnomalyEvent",
    "AnomalyFeatures",
    "CONTRACT_MODELS",
    "ContractModel",
    "DecisionProposal",
    "DeploymentEvent",
    "FinOpsContext",
    "FinOpsRecommendation",
    "Incident",
    "IncidentMemory",
    "IncidentStatus",
    "MOCK_MODELS",
    "Metrics",
    "ObservedUsage",
    "RCAResult",
    "RecoveryMetrics",
    "RecoveryResult",
    "ResourceSpec",
    "ScaleOption",
    "TelemetrySnapshot",
]
