import pytest
from pydantic import ValidationError
from agent_lab.schemas.scout_output import ScoutOutput, Hotspot

def test_scout_output_rejects_missing_fields():
    with pytest.raises(ValidationError):
        ScoutOutput()

def test_hotspot_rejects_invalid_severity():
    with pytest.raises(ValidationError):
        Hotspot(file="x", severity="critical")
