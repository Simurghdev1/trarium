import json

from trarium.structured import (
    translate_code_text,
    translate_csv_text,
    translate_json_text,
    translate_notebook_text,
)


class FakePipeline:
    def translate_block(self, text):
        return "\n".join("TR: " + line if line.strip() else line for line in str(text).splitlines())


PIPELINE = FakePipeline()


def test_json_keys_stay_and_values_are_translated():
    source = json.dumps({"name": "Hello world", "nested": {"title": "Second line"}, "count": 3}, ensure_ascii=False)
    result = json.loads(translate_json_text(source, PIPELINE))
    assert list(result) == ["name", "nested", "count"]
    assert result["name"] == "TR: Hello world"
    assert result["nested"]["title"] == "TR: Second line"
    assert "title" in result["nested"]
    assert result["count"] == 3


def test_csv_keeps_columns_and_numbers():
    source = "Title,Views\nHello world,42\n"
    result = translate_csv_text(source, ",", PIPELINE)
    rows = [line.split(",") for line in result.strip().splitlines()]
    assert rows[0] == ["TR: Title", "TR: Views"]
    assert rows[1] == ["TR: Hello world", "42"]


def test_notebook_keeps_code_cells_untouched():
    notebook = {
        "cells": [
            {"cell_type": "markdown", "source": ["# Hello\n", "Some text\n"], "metadata": {}},
            {"cell_type": "code", "source": ["print('Hello')\n"], "metadata": {}, "outputs": []},
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    result = json.loads(translate_notebook_text(json.dumps(notebook), PIPELINE))
    assert "".join(result["cells"][0]["source"]).startswith("TR: # Hello")
    assert result["cells"][1]["source"] == ["print('Hello')\n"]


def test_code_translates_comments_only():
    source = "def add(a, b):\n    # return the sum\n    return a + b\n"
    result = translate_code_text(source, PIPELINE)
    assert "def add(a, b):" in result
    assert "return a + b" in result
    assert "# TR: return the sum" in result
