from __future__ import annotations

import pytest

from avtools.postgres.client import PostgresClient


class Obj:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_get_pk_chooses_first_available_identifier() -> None:
    assert PostgresClient._get_pk(Obj(code="C")) == "C"
    assert PostgresClient._get_pk(Obj(equipment_no="E")) == "E"
    assert PostgresClient._get_pk(Obj(equipmentno="E2")) == "E2"
    assert PostgresClient._get_pk(Obj(device_name="D")) == "D"
    assert PostgresClient._get_pk(Obj(name="N")) == "N"


def test_get_pk_raises_when_no_identifier_present() -> None:
    with pytest.raises(ValueError):
        PostgresClient._get_pk(Obj())
