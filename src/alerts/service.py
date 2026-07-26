"""Local evaluation of simple user alert rules."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

from src.event_knowledge import ResearchEvent
from src.master_data import MasterDataService
from src.portfolio_risk import PortfolioSnapshot
from src.user_space import UserContext, UserSpaceService

from .models import (
    ALERT_PENDING,
    RULE_EVENT_TYPE,
    RULE_MAX_WEIGHT_ABOVE,
    RULE_PRICE_ABOVE,
    RULE_PRICE_BELOW,
    Alert,
    AlertRule,
)
from .sqlite_repository import SQLiteAlertRepository


class AlertService:
    """Create simple rules and evaluate explicitly supplied observations."""

    def __init__(
        self,
        repository: SQLiteAlertRepository,
        user_space: UserSpaceService,
        master_data: MasterDataService,
    ) -> None:
        self.repository = repository
        self.user_space = user_space
        self.master_data = master_data
        self.repository.initialize()

    def create_price_rule(
        self,
        context: UserContext,
        *,
        asset_id: str,
        rule_type: str,
        threshold: float,
    ) -> AlertRule:
        """Create price_above or price_below."""

        if rule_type not in {RULE_PRICE_ABOVE, RULE_PRICE_BELOW}:
            raise ValueError("Price rule must be price_above or price_below")
        self._require_asset(asset_id)
        return self._create_rule(
            context,
            rule_type=rule_type,
            asset_id=asset_id,
            config={"threshold": float(threshold)},
        )

    def create_event_rule(
        self,
        context: UserContext,
        *,
        event_type: str,
        asset_id: str | None = None,
    ) -> AlertRule:
        """Create a rule for one event type and optional asset."""

        if asset_id is not None:
            self._require_asset(asset_id)
        return self._create_rule(
            context,
            rule_type=RULE_EVENT_TYPE,
            asset_id=asset_id,
            config={"event_type": event_type},
        )

    def create_max_weight_rule(
        self,
        context: UserContext,
        *,
        threshold: float,
    ) -> AlertRule:
        """Create a portfolio maximum weight rule."""

        if threshold < 0 or threshold > 1:
            raise ValueError("max weight threshold must be between 0 and 1")
        return self._create_rule(
            context,
            rule_type=RULE_MAX_WEIGHT_ABOVE,
            asset_id=None,
            config={"threshold": float(threshold)},
        )

    def evaluate_price(
        self,
        context: UserContext,
        *,
        asset_id: str,
        price: float,
        observed_at: str,
    ) -> list[Alert]:
        """Evaluate enabled price rules for one observation."""

        self._require_user(context)
        self._require_asset(asset_id)
        generated: list[Alert] = []
        for rule in self.repository.list_rules(
            context.user_id,
            enabled_only=True,
        ):
            if rule.asset_id != asset_id:
                continue
            threshold = float(rule.config.get("threshold", 0))
            triggered = (
                rule.rule_type == RULE_PRICE_ABOVE and price > threshold
            ) or (
                rule.rule_type == RULE_PRICE_BELOW and price < threshold
            )
            if triggered:
                generated.extend(
                    self._save_if_new(
                        rule,
                        asset_id=asset_id,
                        triggered_at=observed_at,
                        dedupe_key=f"price:{rule.rule_id}:{observed_at}",
                        title="价格阈值触发",
                        message=f"价格 {price} 触发 {rule.rule_type} {threshold}",
                        payload={"price": price, "threshold": threshold},
                    )
                )
        return generated

    def evaluate_event(
        self,
        context: UserContext,
        event: ResearchEvent,
    ) -> list[Alert]:
        """Evaluate event type rules."""

        self._require_user(context)
        generated: list[Alert] = []
        for rule in self.repository.list_rules(
            context.user_id,
            rule_type=RULE_EVENT_TYPE,
            enabled_only=True,
        ):
            if rule.asset_id is not None and rule.asset_id != event.asset_id:
                continue
            if rule.config.get("event_type") != event.event_type:
                continue
            generated.extend(
                self._save_if_new(
                    rule,
                    asset_id=event.asset_id,
                    triggered_at=event.event_date,
                    dedupe_key=f"event:{rule.rule_id}:{event.event_id}",
                    title="关注事件触发",
                    message=event.title,
                    payload={
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "source_url": event.source_url,
                    },
                )
            )
        return generated

    def evaluate_portfolio(
        self,
        context: UserContext,
        snapshot: PortfolioSnapshot,
    ) -> list[Alert]:
        """Evaluate portfolio maximum weight rules."""

        self._require_user(context)
        if snapshot.user_id != context.user_id:
            raise ValueError("Portfolio snapshot belongs to another user")
        generated: list[Alert] = []
        for rule in self.repository.list_rules(
            context.user_id,
            rule_type=RULE_MAX_WEIGHT_ABOVE,
            enabled_only=True,
        ):
            threshold = float(rule.config["threshold"])
            if snapshot.max_weight > threshold:
                generated.extend(
                    self._save_if_new(
                        rule,
                        asset_id=None,
                        triggered_at=snapshot.as_of_date,
                        dedupe_key=(
                            f"portfolio:{rule.rule_id}:{snapshot.snapshot_id}"
                        ),
                        title="组合集中度触发",
                        message=(
                            f"最大权重 {snapshot.max_weight:.4f} "
                            f"高于阈值 {threshold:.4f}"
                        ),
                        payload={
                            "snapshot_id": snapshot.snapshot_id,
                            "max_weight": snapshot.max_weight,
                            "threshold": threshold,
                        },
                    )
                )
        return generated

    def list_rules(self, context: UserContext) -> list[AlertRule]:
        self._require_user(context)
        return self.repository.list_rules(context.user_id)

    def list_alerts(
        self,
        context: UserContext,
        *,
        status: str | None = None,
    ) -> list[Alert]:
        self._require_user(context)
        return self.repository.list_alerts(context.user_id, status=status)

    def acknowledge(
        self,
        context: UserContext,
        alert_id: str,
    ) -> Alert:
        self._require_user(context)
        return self.repository.acknowledge(
            context.user_id,
            alert_id,
            _now(),
        )

    def _create_rule(
        self,
        context: UserContext,
        *,
        rule_type: str,
        asset_id: str | None,
        config: dict[str, object],
    ) -> AlertRule:
        self._require_user(context)
        now = _now()
        rule = AlertRule(
            rule_id=f"rule:{uuid4()}",
            user_id=context.user_id,
            asset_id=asset_id,
            rule_type=rule_type,
            config=config,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        self.repository.insert_rule(rule)
        return rule

    def _save_if_new(
        self,
        rule: AlertRule,
        *,
        asset_id: str | None,
        triggered_at: str,
        dedupe_key: str,
        title: str,
        message: str,
        payload: dict[str, object],
    ) -> list[Alert]:
        alert = Alert(
            alert_id=f"alert:{uuid5(NAMESPACE_URL, dedupe_key)}",
            rule_id=rule.rule_id,
            user_id=rule.user_id,
            asset_id=asset_id,
            title=title,
            message=message,
            triggered_at=triggered_at,
            dedupe_key=dedupe_key,
            status=ALERT_PENDING,
            payload=payload,
            created_at=_now(),
        )
        stored, inserted = self.repository.save_alert(alert)
        return [stored] if inserted else []

    def _require_user(self, context: UserContext) -> None:
        if self.user_space.get_user(context.user_id) is None:
            raise KeyError(f"Unknown user_id: {context.user_id}")

    def _require_asset(self, asset_id: str) -> None:
        if self.master_data.get_asset(asset_id) is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
