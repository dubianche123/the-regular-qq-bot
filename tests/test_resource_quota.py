from unittest.mock import Mock, patch

import config
from regular.resource_quota import ResourceQuotas
from storage import DailyBudget, StateStore


def make_quota():
    state = {}
    day = ["2026-11-01"]
    mark, save = Mock(), Mock()
    return ResourceQuotas(state, mark, save, today=lambda: day[0]), state, day, mark, save


def test_personal_limits_follow_relation_and_exception_is_real():
    quota, state, _, _, save = make_quota()
    with patch.object(config, "IMAGE_USER_DAILY_CAP", 2), \
         patch.object(config, "IMAGE_USER_EXTRA_FRIEND", 2):
        assert quota.consume("image", "group", "friend", 30) == "normal"
        assert quota.consume("image", "group", "friend", 30) == "normal"
        assert quota.check("image", "group", "friend", 30) == "exception"
        assert quota.consume("image", "group", "friend", 30) == "exception"
        assert quota.consume("image", "group", "friend", 30) == "extra"
        assert quota.consume("image", "group", "friend", 30) == "exhausted"
        assert save.call_count == 4
        assert state["counts"]["image"]["group"]["friend"] == 4


def test_group_user_resource_and_day_are_independent():
    quota, _, day, _, _ = make_quota()
    with patch.object(config, "IMAGE_USER_DAILY_CAP", 1), \
         patch.object(config, "SEARCH_USER_DAILY_CAP", 1):
        assert quota.consume("image", "group-a", "user-a", 0) == "normal"
        assert quota.check("image", "group-a", "user-a", 0) == "exhausted"
        assert quota.check("image", "group-a", "user-b", 0) == "normal"
        assert quota.check("image", "group-b", "user-a", 0) == "normal"
        assert quota.check("search", "group-a", "user-a", 0) == "normal"
        day[0] = "2026-11-02"
        assert quota.consume("image", "group-a", "user-a", 0) == "normal"
        assert quota.check("image", "group-a", "user-a", 0) == "exhausted"


def test_failed_save_is_unavailable_and_reserves_conservatively():
    quota, _, _, mark, save = make_quota()
    save.side_effect = OSError()
    with patch.object(config, "SEARCH_USER_DAILY_CAP", 1):
        assert quota.consume("search", "group", "user", 0) == "unavailable"
        assert quota.check("search", "group", "user", 0) == "exhausted"
    mark.assert_called_once()


def test_missing_identity_never_consumes():
    quota, _, _, _, save = make_quota()
    assert quota.consume("image", "group", "", 0) == "unavailable"
    save.assert_not_called()


def test_personal_and_global_reservation_are_saved_together(tmp_path):
    path = tmp_path / "state.json"
    state = StateStore(str(path), 1)
    global_budget = DailyBudget(3, state.data.setdefault("image_generation_usage", {}))
    quota = ResourceQuotas(state.data.setdefault("resource_quotas", {}),
                           state.mark_dirty, state.save_now, today=DailyBudget.today)
    assert quota.check("image", "group", "user", 0) == "normal"
    assert global_budget.try_consume()
    assert quota.consume("image", "group", "user", 0) == "normal"
    reloaded = StateStore(str(path), 1)
    assert reloaded.data["image_generation_usage"]["used"] == 1
    assert reloaded.data["resource_quotas"]["counts"]["image"]["group"]["user"] == 1
