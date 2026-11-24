import json

from src.clinical_guidelines import ClinicalGuidelinesManager
from src.models import REDCapField
from src.schema_builder import SchemaBuilder
from src.ai_extractor import BaseAIExtractor


class DummyExtractor(BaseAIExtractor):
    """Concrete dummy extractor for testing prompt building."""
    def is_available(self) -> bool:
        return True

    def _call_llm(self, system_prompt: str, user_prompt: str, json_schema: dict) -> str:
        return "{}"


def test_clinical_guidelines_manager_defaults(tmp_path):
    cfg_file = tmp_path / "clinical_guidelines.json"
    mgr = ClinicalGuidelinesManager(config_path=cfg_file)

    # Should create file with defaults
    assert cfg_file.exists()
    assert "anatomia_patologica_preoperatoria" in mgr.list_forms()
    assert "result_cb" in mgr.get_field_guidelines("anatomia_patologica_preoperatoria")

    rule = mgr.get_field_guideline("result_cb", "anatomia_patologica_preoperatoria")
    assert rule is not None
    assert "Tru-cut" in rule or "B1" in rule


def test_clinical_guidelines_manager_custom_override(tmp_path):
    cfg_file = tmp_path / "clinical_guidelines.json"
    custom_content = {
        "version": "1.0",
        "forms": {
            "anatomia_patologica_preoperatoria": {
                "field_guidelines": {
                    "result_cb": "Regola personalizzata: estrai sempre B5 per invasivo."
                }
            }
        }
    }
    cfg_file.write_text(json.dumps(custom_content), encoding="utf-8")

    mgr = ClinicalGuidelinesManager(config_path=cfg_file)
    # The custom rule must override the default
    assert mgr.get_field_guideline("result_cb", "anatomia_patologica_preoperatoria") == "Regola personalizzata: estrai sempre B5 per invasivo."
    # Other forms must still be present from defaults due to deep merge
    assert "struttura_della_lesione" in mgr.list_forms()


def test_clinical_guidelines_add_and_remove(tmp_path):
    cfg_file = tmp_path / "guidelines.json"
    mgr = ClinicalGuidelinesManager(config_path=cfg_file)

    mgr.set_field_guideline("dati_del_paziente", "test_field", "Istruzione speciale di test")
    assert mgr.get_field_guideline("test_field", "dati_del_paziente") == "Istruzione speciale di test"

    # Verify persisted to file
    with open(cfg_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["forms"]["dati_del_paziente"]["field_guidelines"]["test_field"] == "Istruzione speciale di test"

    # Remove
    deleted = mgr.remove_field_guideline("dati_del_paziente", "test_field")
    assert deleted is True
    assert mgr.get_field_guideline("test_field", "dati_del_paziente") is None


def test_schema_builder_injects_guidelines(tmp_path):
    cfg_file = tmp_path / "guidelines.json"
    mgr = ClinicalGuidelinesManager(config_path=cfg_file)
    mgr.set_field_guideline(
        "anatomia_patologica_preoperatoria",
        "result_cb",
        "Estrai esito core_biopsy in base all'esame (VAAB, tru-cut): 1=B1, 2=B2, 3=B3, 4=B4, 5=B5"
    )

    fields = [
        REDCapField(
            field_name="result_cb",
            form_name="anatomia_patologica_preoperatoria",
            field_type="dropdown",
            field_label="Esito core biopsy",
            select_choices_or_calculations="0, Non eseguito | 1, B1 | 2, B2 | 3, B3 | 4, B4 | 5, B5"
        ),
        REDCapField(
            field_name="date_of_cb",
            form_name="anatomia_patologica_preoperatoria",
            field_type="text",
            field_label="Data core biopsy",
            text_validation_type_or_show_slider_number="date_ymd"
        )
    ]

    # Test prompt guidelines
    prompt_text = SchemaBuilder.build_prompt_guidelines(
        fields,
        form_name="anatomia_patologica_preoperatoria",
        guidelines_mgr=mgr
    )
    assert "### LINEE GUIDA CLINICHE SPECIFICHE:" in prompt_text
    assert "GUIDA CLINICA: Estrai esito core_biopsy in base all'esame (VAAB, tru-cut)" in prompt_text

    # Test JSON schema descriptions
    schema = SchemaBuilder.build_json_schema(
        fields,
        enforce_all_required=True,
        form_name="anatomia_patologica_preoperatoria",
        guidelines_mgr=mgr
    )
    assert "properties" in schema
    assert "result_cb" in schema["properties"]
    desc = schema["properties"]["result_cb"]["description"]
    assert "GUIDA CLINICA: Estrai esito core_biopsy" in desc


def test_ai_extractor_passes_form_name_to_schema_builder(tmp_path):
    cfg_file = tmp_path / "guidelines.json"
    mgr = ClinicalGuidelinesManager(config_path=cfg_file)
    mgr.set_field_guideline(
        "struttura_della_lesione",
        "first_breast_operation",
        "Chirurgia senologica: 5=Quadrantectomia, 6=Mastectomia."
    )
    # Set as active singleton instance for this test
    old_instance = ClinicalGuidelinesManager._instance
    ClinicalGuidelinesManager._instance = mgr

    try:
        fields = [
            REDCapField(
                field_name="first_breast_operation",
                form_name="struttura_della_lesione",
                field_type="dropdown",
                field_label="Primo intervento",
                select_choices_or_calculations="5, Quadrantectomia | 6, Mastectomia"
            )
        ]

        extractor = DummyExtractor()
        system_prompt, user_prompt, json_schema = extractor.build_prompts(
            fields,
            report_text="Eseguita quadrantectomia sinistra.",
            form_name="struttura_della_lesione"
        )

        assert "GUIDA CLINICA: Chirurgia senologica: 5=Quadrantectomia, 6=Mastectomia" in user_prompt
        assert "GUIDA CLINICA: Chirurgia senologica" in json_schema["properties"]["first_breast_operation"]["description"]
    finally:
        ClinicalGuidelinesManager._instance = old_instance
