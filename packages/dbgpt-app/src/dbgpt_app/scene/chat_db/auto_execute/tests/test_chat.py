"""Tests for governed ChatDB scene integration."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from dbgpt_app.scene.chat_db.auto_execute.chat import (
    ChatWithDbAutoExecute,
    _resolve_catalog_path,
)
from dbgpt_app.scene.chat_db.auto_execute.config import ChatWithDBExecuteConfig
from dbgpt_app.scene.chat_db.auto_execute.out_parser import SqlAction
from dbgpt_app.scene.chat_db.safe_sql import GuardedQueryResult, PreparedSql

PROJECT_DIR = Path(__file__).resolve().parents[8] / "projects" / "qingpu_chatdb"
CATALOG_PATH = PROJECT_DIR / "config" / "catalog.yaml"


def _prepared():
    return PreparedSql(
        original_sql="SELECT dt FROM green_test.ads_greenhouse_indicator",
        sql="SELECT dt FROM main.ads_greenhouse_indicator LIMIT 50",
        dialect="sqlite",
        referenced_tables=("green_test.ads_greenhouse_indicator",),
        referenced_columns=("green_test.ads_greenhouse_indicator.dt",),
        limit=50,
        rewrites=(),
    )


def test_config_requires_catalog_by_default():
    config = ChatWithDBExecuteConfig()

    assert config.catalog_path is None
    assert config.max_num_results == 50


def test_resolve_catalog_path_rejects_missing_config():
    with pytest.raises(ValueError, match="requires app.configs.catalog_path"):
        _resolve_catalog_path(None)


def test_resolve_catalog_path_accepts_absolute_path():
    assert _resolve_catalog_path(str(CATALOG_PATH)) == CATALOG_PATH.resolve()


@pytest.mark.asyncio
async def test_generate_input_values_uses_catalog_context_only():
    catalog = Mock()
    catalog.render_prompt_context.return_value = "governed catalog"
    scene = SimpleNamespace(
        db_name="qingpu",
        current_user_input=SimpleNamespace(last_text="查询平均温度"),
        curr_config=SimpleNamespace(max_num_results=50),
        database=SimpleNamespace(dialect="sqlite"),
        _catalog=catalog,
        _generate_numbered_list=lambda: "1. Table",
    )

    result = await ChatWithDbAutoExecute.generate_input_values(scene)

    assert result["table_info"] == "governed catalog"
    assert result["top_k"] == 50
    catalog.render_prompt_context.assert_called_once_with()


def test_do_action_returns_guarded_result():
    expected = GuardedQueryResult(pd.DataFrame([{"dt": "2026-06-01"}]), _prepared())
    executor = Mock()
    executor.execute.return_value = expected
    scene = SimpleNamespace(_sql_executor=executor)
    action = SqlAction(
        sql="SELECT dt FROM green_test.ads_greenhouse_indicator",
        thoughts={},
        display="response_table",
        direct_response="",
    )

    result = ChatWithDbAutoExecute.do_action(scene, action)

    assert result is expected
    executor.execute.assert_called_once_with(action.sql)


def test_do_action_direct_response_does_not_execute():
    executor = Mock()
    scene = SimpleNamespace(_sql_executor=executor)
    action = SqlAction(sql="", thoughts={}, display="", direct_response="hello")

    assert ChatWithDbAutoExecute.do_action(scene, action) is None
    executor.execute.assert_not_called()
