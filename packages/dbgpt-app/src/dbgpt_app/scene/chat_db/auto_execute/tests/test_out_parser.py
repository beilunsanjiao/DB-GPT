"""Tests for rendering governed ChatDB results."""

import json
import xml.etree.ElementTree as ET
from unittest.mock import Mock

import pandas as pd
import pytest

from dbgpt_app.scene.chat_db.auto_execute.out_parser import (
    DbChatOutputParser,
    SqlAction,
)
from dbgpt_app.scene.chat_db.safe_sql import GuardedQueryResult, PreparedSql


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


def _sql_action():
    return SqlAction(
        sql="SELECT dt FROM green_test.ads_greenhouse_indicator",
        thoughts={"speak": "result"},
        display="response_table",
        direct_response="",
    )


def test_parser_serializes_guarded_result_without_executing_again():
    parser = DbChatOutputParser()
    guarded = GuardedQueryResult(
        dataframe=pd.DataFrame([{"dt": "2026-06-01"}]),
        prepared=_prepared(),
    )

    output = parser.parse_view_response("result", guarded, _sql_action())
    element = ET.fromstring(output.split("\n", 1)[1])
    content = json.loads(element.attrib["content"])

    assert content == {
        "type": "response_table",
        "sql": "SELECT dt FROM main.ads_greenhouse_indicator LIMIT 50",
        "generated_sql": "SELECT dt FROM green_test.ads_greenhouse_indicator",
        "data": [{"dt": "2026-06-01"}],
    }


def test_missing_thoughts_and_display_use_safe_defaults():
    parser = DbChatOutputParser()

    action = parser.parse_prompt_response(
        '{"sql":"SELECT dt FROM green_test.ads_greenhouse_indicator"}'
    )

    assert action.thoughts == {}
    assert action.display == "response_table"


def test_pure_sql_uses_table_display_default():
    parser = DbChatOutputParser()

    action = parser.parse_prompt_response(
        "SELECT dt FROM green_test.ads_greenhouse_indicator"
    )

    assert action.display == "response_table"


@pytest.mark.parametrize("data", [None, pd.DataFrame()])
def test_parser_rejects_ungoverned_sql_results(data):
    parser = DbChatOutputParser()

    with pytest.raises(Exception, match="Generate view content failed"):
        parser.parse_view_response("result", data, _sql_action())


def test_parser_never_calls_legacy_callable():
    parser = DbChatOutputParser()
    legacy_callable = Mock()

    with pytest.raises(Exception, match="Generate view content failed"):
        parser.parse_view_response("result", legacy_callable, _sql_action())

    legacy_callable.assert_not_called()


def test_direct_response_remains_compatible():
    parser = DbChatOutputParser()
    action = SqlAction(sql="", thoughts={}, display="", direct_response="hello")

    assert parser.parse_view_response("ignored", None, action) == "hello\n"
