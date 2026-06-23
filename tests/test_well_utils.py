"""Tests for well range parsing and validation."""

from __future__ import annotations

import pytest

from labquery.well_utils import expand_well_list, parse_well_range, validate_wells


class TestParseWellRange:
    def test_single_well(self):
        assert parse_well_range("A1") == ["A1"]

    def test_single_well_case_insensitive(self):
        assert parse_well_range("a1") == ["A1"]

    def test_row_range(self):
        assert parse_well_range("A1-A6") == ["A1", "A2", "A3", "A4", "A5", "A6"]

    def test_column_range(self):
        assert parse_well_range("A1-H1") == [
            "A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1",
        ]

    def test_reversed_row_range(self):
        assert parse_well_range("A6-A1") == ["A1", "A2", "A3", "A4", "A5", "A6"]

    def test_reversed_column_range(self):
        assert parse_well_range("H1-A1") == [
            "A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1",
        ]

    def test_single_well_range(self):
        assert parse_well_range("A1-A1") == ["A1"]

    def test_two_digit_column(self):
        assert parse_well_range("A10-A12") == ["A10", "A11", "A12"]

    def test_diagonal_range_raises(self):
        with pytest.raises(ValueError, match="both row and column change"):
            parse_well_range("A1-B3")

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="Invalid well specification"):
            parse_well_range("ZZZ")

    def test_whitespace_stripped(self):
        assert parse_well_range("  A1  ") == ["A1"]


class TestValidateWells:
    def test_all_valid(self):
        assert validate_wells(["A1", "B6", "H12"]) == []

    def test_invalid_row(self):
        assert validate_wells(["Z1"]) == ["Z1"]

    def test_invalid_column(self):
        assert validate_wells(["A13"]) == ["A13"]

    def test_column_zero(self):
        assert validate_wells(["A0"]) == ["A0"]

    def test_custom_max(self):
        assert validate_wells(["D5"], max_row="C", max_col=4) == ["D5"]
        assert validate_wells(["C4"], max_row="C", max_col=4) == []

    def test_garbage_input(self):
        assert validate_wells(["not_a_well"]) == ["NOT_A_WELL"]


class TestExpandWellList:
    def test_individual_wells(self):
        assert expand_well_list(["A1", "B2", "C3"]) == ["A1", "B2", "C3"]

    def test_range_expansion(self):
        assert expand_well_list(["A1-A3", "B1"]) == ["A1", "A2", "A3", "B1"]

    def test_multiple_ranges(self):
        assert expand_well_list(["A1-A2", "B1-B2"]) == ["A1", "A2", "B1", "B2"]

    def test_empty_list(self):
        assert expand_well_list([]) == []
