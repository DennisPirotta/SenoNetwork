# Mappatura Struttura Progetto REDCap: SenoNetwork

**Progetto**: SenoNetwork (ID: 20)  
**Totale Strumenti / Form**: 8  
**Totale Campi**: 292

---

## 📋 Form: `dati_del_paziente` (Dati Del Paziente)
**Scopo Clinico**: Anagrafica paziente, comorbidità (ipertensione, diabete, cardiopatia, tiroide), familiarità e genetica (BRCA1/2), metastasi a distanza e follow-up.  
**Campi totali**: 45 | **Campi estraibili per l'IA**: 44

| Variabile / Campo | Tipo | Etichetta Clinica | Scelte / Validazione |
|---|---|---|---|
| `record_id` | `text` | Record ID |  |
| `surname` | `text` | Cognome |  |
| `name` | `text` | Nome |  |
| `date_of_birth` | `text` | Data di nascita | Val: `date_ymd`  |
| `year_of_birth` | `calc` | Anno di nascita | Opzioni: year([date_of_birth]) |
| `gender` | `dropdown` | Genere | Val: `autocomplete` Opzioni: W, Femmina | M, Maschio |
| `familiarity_for_breast_cancer` | `dropdown` | Familiarità per cancro alla mammella | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `other_neoplasia` | `notes` | Altra neoplasia |  |
| `genetic_assessment` | `dropdown` | Indagine genetica | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `brca1` | `dropdown` | BRCA1 | Val: `autocomplete` Opzioni: 0, Negativo | 1, Positivo | 9, Ignoto |
| `brca2` | `dropdown` | BRCA2 | Val: `autocomplete` Opzioni: 0, Negativo | 1, Positivo | 9, Ignoto |
| `hypertension` | `dropdown` | Ipertensione | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `diabetes` | `dropdown` | Diabete | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `ischemic_heart_disease` | `dropdown` | Cardiopatia ischemica | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `hypo_hyperthyroidism` | `dropdown` | Ipo/ipertiroidismo | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `autoimmune_diseases` | `dropdown` | Patologie autoimmunitarie | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `other_comorbidities` | `dropdown` | Altra comorbidità | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `previous_breast_lesion` | `dropdown` | Precedente lesione mammaria | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `date_previous_breast_lesion` | `text` | Data precedente carcinoma mammario | Val: `date_ymd`  |
| `diagnosis_previous_breast_lesion` | `dropdown` | Diagnosi precedente carcinoma mammario | Val: `autocomplete` Opzioni: 0, Benigno | 1, Lesione intraepiteliale | 2, In situ | 3,... |
| `status` | `dropdown` | Stato in vita | Val: `autocomplete` Opzioni: 1, Viva | 2, Deceduta | 9, Ignoto |
| `date_of_follow_up_update` | `text` | Data di aggiornamento follow-up | Val: `date_ymd`  |
| `illness_status` | `dropdown` | Stato malattia | Val: `autocomplete` Opzioni: 1, NED (No Evidence of Disease) | 2, Remissione completa ... |
| `primary_cause_of_death` | `dropdown` | Causa di morte | Val: `autocomplete` Opzioni: 1, Cancro mammario | 2, Altra causa | 9, Ignota |
| `presence_of_distant_metastases` | `dropdown` | Presenza di metastasi a distanza | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `date_of_appearance_of_first_distant_metastasis` | `text` | Data comparsa prima metastasi a distanza | Val: `date_ymd`  |
| `bone_metastases` | `dropdown` | Metastasi allo scheletro | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `lung_metastases` | `dropdown` | Metastasi al polmone | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `liver_metastases` | `dropdown` | Metastasi al fegato | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `cns` | `dropdown` | Metastasi SNC | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `metastases_in_other_site` | `dropdown` | Metastasi in altra sede | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `other_site_of_mts` | `text` | Altra sede metastasi |  |
| `side_of_lesion` | `dropdown` | Lato della lesione | Val: `autocomplete` Opzioni: L, Sinistro | R, Destro | N, Ignoto |
| `site_of_the_lesion` | `dropdown` | Sede della lesione | Val: `autocomplete` Opzioni: 1, Supero-esterno | 2, Esterno-centrale | 3, Infero-ester... |
| `classification_with_respect_to_other_lesions` | `dropdown` | Stato della lesione | Val: `autocomplete` Opzioni: 1, Unica o principale | 2, Sincrona controlaterale | 3, S... |
| `exclusion` | `dropdown` | Eleggibile per esclusione dalla valutazione | Val: `autocomplete` Opzioni: 0, No | 1, Trattamento iniziato altrove | 2, Lesione non ... |
| `mdm_prior_to_treatment` | `dropdown` | Discussione caso in riunione multidisciplinare pre-trattamento | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `date_of_mdm_prior_to_treatment` | `text` | Data discussione caso pre-trattamento | Val: `date_ymd`  |
| `mdm_after_treatment` | `dropdown` | Discussione caso in riunione multidisciplinare post-trattamento | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `date_of_mdm_after_treatment` | `text` | Data discussione caso post-trattamento | Val: `date_ymd`  |
| `bra_size` | `dropdown` | Taglia del reggiseno | Val: `autocomplete` Opzioni: 1, Prima misura | 2, Seconda misura | 3, Terza misura | 4... |
| `menstrual_status` | `dropdown` | Stato ormonale | Val: `autocomplete` Opzioni: 1, Fertile | 2, Gravidanza | 3, Menopausa | 4, Terapia so... |
| `participation_in_clinical_trials` | `dropdown` | Partecipazione a trial | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `psychological_assessment` | `dropdown` | Valutazione psicologica | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `support_by_assistance_group` | `dropdown` | Supporto associazione dei malati | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |

---

## 📋 Form: `radiologia` (Radiologia)
**Scopo Clinico**: Indagini diagnostiche senologiche: Mammografia 2D/3D tomosintesi (esito EU R1-R5, BI-RADS 0-6), Ecografia mammaria (U1-U5), Risonanza Magnetica (MR1-MR5), stadio clinico cTNM e indicazioni.  
**Campi totali**: 35 | **Campi estraibili per l'IA**: 34

| Variabile / Campo | Tipo | Etichetta Clinica | Scelte / Validazione |
|---|---|---|---|
| `text_diagnostica` | `descriptive` | <div class="rich-text-field-label"><p>Nominativo del paziente:    |  |
| `case_classification_in_screened_patient` | `dropdown` | Classificazione del caso in rapporto allo screening | Val: `autocomplete` Opzioni: 1, Screen detected | 2, Adesione spontanea | 3, Intervall... |
| `date_latest_test_1st_level` | `text` | Data ultimo test I livello | Val: `date_ymd`  |
| `source_of_referral` | `dropdown` | Provenienza caso | Val: `autocomplete` Opzioni: 1, Programma screening | 2, Altro | 9, Ignoto |
| `palpable_lesion` | `dropdown` | Lesione palpabile | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `result` | `dropdown` | Esito | Val: `autocomplete` Opzioni: 0, Non eseguito | 1, Nessun segno fisico | 2, Probabilmen... |
| `size_mm_ce` | `text` | Estensione (mm.) all'esame clinico | Val: `integer`  |
| `date_of_mx` | `text` | Data mammografia | Val: `date_ymd`  |
| `d_mx_performance` | `dropdown` | Esecuzione mammografia 2D | Val: `autocomplete` Opzioni: 1, Eseguita | 0, Non eseguita | 2, Non eseguita ma ricost... |
| `date_of_tomosynthesis` | `text` | Data tomosintesi | Val: `date_ymd`  |
| `tomosynthesis_performed` | `dropdown` | Esecuzione tomosintesi | Val: `autocomplete` Opzioni: 1, Eseguita | 0, Non eseguita | 9, Ignoto |
| `result_of_mx_eu` | `dropdown` | Esito mammografia (classificazione europea) | Val: `autocomplete` Opzioni: 1, R1-Negativo | 2, R2-Benigno | 3, R3-Dubbio | 4, R4-Sos... |
| `result_of_mx_birads` | `dropdown` | Esito mammografia (classificazione BIRADS) | Val: `autocomplete` Opzioni: 0, 0-Necessità di ulteriori viste | 1, 1-Negativo | 2, 2-... |
| `type_of_cancer` | `dropdown` | Tipo cancro | Val: `autocomplete` Opzioni: 1, Probabile in situ | 2, Probabile invasivo | 9, Ignoto |
| `presence_of_microcalcifications` | `dropdown` | Presenza di microcalcificazioni | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `significant_pattern_additional` | `dropdown` | Pattern significativo (oltre alle microcalcificazioni) | Val: `autocomplete` Opzioni: 0, Nessuno | 1, Opacità regolare (tondeggiante/ovalare) |... |
| `focality_at_mx` | `dropdown` | Focalità alla mammografia | Val: `autocomplete` Opzioni: 0, Unica | 1, Multifocale | 2, Multicentrica | 9, Ignoto |
| `extension_mm_mx` | `text` | Estensione (mm.) alla mammografia | Val: `integer`  |
| `total_extension_mm` | `text` | Estensione totale (mm.) alla mammografia | Val: `integer`  |
| `date_of_us` | `text` | Data ecografia | Val: `date_ymd`  |
| `result_of_us` | `dropdown` | Esito ecografia | Val: `autocomplete` Opzioni: 0, Non eseguita | 1, U1-Negativo | 2, U2-Benigno | 3, U3-... |
| `extension_mm_us` | `text` | Estensione (mm.) all'ecografia | Val: `integer`  |
| `focality_at_us` | `dropdown` | Focalità all'ecografia | Val: `autocomplete` Opzioni: 0, Unica | 1, Multifocale | 2, Multicentrica | 9, Ignoto |
| `cb_technique` | `dropdown` | Tecnica della core biopsy | Val: `autocomplete` Opzioni: 1, Agobiopsia/CB/ago a scatto/Tru-cut | 2, Vacuum assiste... |
| `axillary_us` | `dropdown` | Ecografia ascellare | Val: `autocomplete` Opzioni: 0, Non eseguita | 1, Eseguita negativa | 2, Eseguita sosp... |
| `needle_biopsy` | `dropdown` | Prelievo percutaneo ascellare | Val: `autocomplete` Opzioni: 0, Non eseguito | 1, Inadeguato | 2, Negativo | 3, Dubbio... |
| `mr_result` | `dropdown` | Esito risonanza magnetica | Val: `autocomplete` Opzioni: 0, Non eseguita | 7, MR0-Incompleta | 1, MR1-Negativa | 2... |
| `date_of_mr_imaging` | `text` | Data risonanza magnetica | Val: `date_ymd`  |
| `size_mm_mr` | `text` | Estensione (mm.) | Val: `integer`  |
| `disease_extent_at_mr` | `dropdown` | Focalità alla risonanza magnetica | Val: `autocomplete` Opzioni: 0, Unica | 1, Multifocale | 2, Multicentrica | 9, Ignoto |
| `t_by_imaging_or_clinical_examination` | `dropdown` | T strumentale o clinico | Val: `autocomplete` Opzioni: X, X | 0, 0 | IS, IS | 1, 1 | 1a, 1a | 1b, 1b | 1c, 1c | ... |
| `n_regional_nodes_affected` | `dropdown` | N (metastasi in linfonodi regionali) | Val: `autocomplete` Opzioni: X, X | 0, 0 | 1, 1 | 1mi, 1mi | 2, 2 | 2a, 2a | 2b, 2b | ... |
| `m_distant_metastases` | `dropdown` | M (metastasi a distanza) | Val: `autocomplete` Opzioni: X, X | 0, 0 | 1, 1 | 9, Ignoto |
| `recommendations` | `dropdown` | Indicazioni conclusive | Val: `autocomplete` Opzioni: 1, Exeresi | 2, Follow-up | 4, Terapia neoadiuvante | 5, ... |
| `date_of_recommendations` | `text` | Data indicazioni conclusive | Val: `date_ymd`  |

---

## 📋 Form: `anatomia_patologica_preoperatoria` (Anatomia Patologica Preoperatoria)
**Scopo Clinico**: Biopsie diagnostiche pre-operatorie: Agoaspirato (FNA C1-C5), Agobiopsia (Core Biopsy B1-B5), istotipo, grado nucleare, recettori ER, PgR, Ki-67 e HER2.  
**Campi totali**: 20 | **Campi estraibili per l'IA**: 19

| Variabile / Campo | Tipo | Etichetta Clinica | Scelte / Validazione |
|---|---|---|---|
| `text_ap_pre_op` | `descriptive` | <div class="rich-text-field-label"><p>Nominativo del paziente:    |  |
| `data_of_fna` | `text` | Data esame citologico agoaspirato (FNA) | Val: `date_ymd`  |
| `cytological_result_fna` | `dropdown` | Esito esame citologico agoaspirato | Val: `autocomplete` Opzioni: 0, Non eseguito | 1, C1-Inadeguato | 2, C2-Cellule epitel... |
| `date_of_cb` | `text` | Data core biopsy | Val: `date_ymd`  |
| `result_cb` | `dropdown` | Esito core biopsy | Val: `autocomplete` Opzioni: 0, Non eseguito | 1, B1-Inadeguato/Normale | 2, B2-Benign... |
| `histological_type_b4_b5` | `dropdown` | Tipo istologico (B4/B5) | Val: `autocomplete` Opzioni: 1, Carcinoma in situ | 2, Carcinoma invasivo | 3, Non val... |
| `istotype` | `dropdown` | Istotipo | Val: `autocomplete` Opzioni: 1, Duttale | 2, Lobulare | 3, Tubulare | 4, Altro | 9, Ig... |
| `nuclear_grade` | `dropdown` | Grado nucleare | Val: `autocomplete` Opzioni: 1, Basso | 2, Medio | 3, Alto | 9, Ignoto |
| `er_immunohistochemical_at_cb` | `dropdown` | Recettori degli estrogeni metodo immunoistochimico | Val: `autocomplete` Opzioni: 0, Non eseguito | 1, Eseguito | 9, Ignoto |
| `er_positive_cells_at_cb` | `text` | Cellule positive al ER (%) | Val: `integer`  |
| `er_status_at_cb` | `dropdown` | Stato ER | Val: `autocomplete` Opzioni: 0, Negativo | 1, Positivo | 9, Ignoto |
| `pr_immunohistochemical_at_cb` | `dropdown` | Recettori del progesterone metodo immunoistochimico | Val: `autocomplete` Opzioni: 0, Non eseguito | 1, Eseguito | 9, Ignoto |
| `pr_positive_cells_at_cb` | `text` | Cellule positive al PgR (%) | Val: `integer`  |
| `pr_status_at_cb` | `dropdown` | Stato PgR | Val: `autocomplete` Opzioni: 0, Negativo | 1, Positivo | 9, Ignoto |
| `proliferation_activity_ki67_immunohistochemical_at_cb` | `dropdown` | Attività proliferativa Ki67 metodo immunoistochimico | Val: `autocomplete` Opzioni: 0, Non eseguito | 1, Eseguito | 9, Ignoto |
| `ki67_positive_cells_at_cb` | `text` | Cellule positive al Ki67 (%) | Val: `integer`  |
| `her2_overexpression_immunohistochemistry_at_cb` | `dropdown` | Iperespressione Her2 metodo immunoistochimico | Val: `autocomplete` Opzioni: 0, Non eseguito | 1, Negativo 0/1+ | 2, Dubbio 2+ | 3, Po... |
| `her2_positive_cells_at_cb` | `text` | Cellule positive al Her2 (%) | Val: `integer`  |
| `fish_hybridization_at_cb` | `dropdown` | Stato gene Her2 | Val: `autocomplete` Opzioni: 2, Non eseguito | 0, Non amplificato | 1, Amplificato | 3... |
| `her2_ratio_at_cb` | `text` | Ratio | Val: `integer`  |

---

## 📋 Form: `struttura_della_lesione` (Struttura Della Lesione)
**Scopo Clinico**: Dati chirurgici: sede lesione (quadranti QSE, QSI, QIE, QII), lateralità (destra/sinistra), tipo di chirurgia (conservativa/mastectomia), oncoplastica, ricostruzione e chirurgia ascellare (sentinella/dissezione).  
**Campi totali**: 53 | **Campi estraibili per l'IA**: 52

| Variabile / Campo | Tipo | Etichetta Clinica | Scelte / Validazione |
|---|---|---|---|
| `text_chirurgia_senologica` | `descriptive` | <div class="rich-text-field-label"><p>Nominativo del paziente:    |  |
| `bi1` | `dropdown` | Esecuzione intervento sulla mammella | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `reason_for_non_intervention` | `dropdown` | Motivo di non intervento | Val: `autocomplete` Opzioni: 1, Paziente non ancora operata | 2, Rifiuto | 3, Motivi d... |
| `admission_date_for_bi1` | `text` | Data ricovero | Val: `date_ymd`  |
| `discharge_date_for_bi1` | `text` | Data dimissione | Val: `date_ymd`  |
| `date_of_bi1` | `text` | Data 1° intervento | Val: `date_ymd`  |
| `specimen_x_ray_at_bi1` | `dropdown` | Esecuzione RX pezzo | Val: `autocomplete` Opzioni: 0, No | 1, Sì 1 proiezione | 2, Sì 2 proiezioni | 3, Sì |... |
| `result_of_x_ray_specimen_at_bi1` | `dropdown` | Esito RX pezzo | Val: `autocomplete` Opzioni: 0, Lesione assente | 1, Lesione presente e centrata | 2, ... |
| `specimen_us_at_bi1` | `dropdown` | Esecuzione ecografia pezzo | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `result_us_specimen_at_bi1` | `dropdown` | Esito ecografia pezzo | Opzioni: 0, Lesione assente | 1, Lesione presente e centrata | 2, ... |
| `frozen_section_of_the_lesion_at_bi1` | `dropdown` | Esame estemporaneo intraoperatorio | Val: `autocomplete` Opzioni: 0, Non eseguito | 1, Sì negativo | 2, Sì dubbio | 3, Sì p... |
| `frozen_section_of_specimen_margins_at_bi1` | `dropdown` | Congelatore margini | Val: `autocomplete` Opzioni: 0, Non eseguito | 1, Sì negativo | 2, Sì Tis in prossimit... |
| `first_breast_operation` | `dropdown` | Primo intervento | Val: `autocomplete` Opzioni: 1, Biopsia incisionale | 2, Duttectomia | 3, Biopsia esci... |
| `type_of_mastectomy_at_bi1` | `dropdown` | Tipo di mastectomia al primo intervento | Val: `autocomplete` Opzioni: 1, Totale | 2, Skin Reducing | 3, Skin Sparing | 4, Nippl... |
| `associated_plastic_surgery_at_bi1` | `dropdown` | Chirurgia plastica associata al primo intervento | Opzioni: 0, No | 1, Rimodellamento | 2, Intervento oncoplastico | ... |
| `type_of_oncoplastic_intervention_at_bi1` | `dropdown` | Tipo di intervento oncoplastico al primo intervento | Val: `autocomplete` Opzioni: 1, Round Block Technique | 2, Mammoplastica orizzontale |... |
| `other_type_of_oncoplastic_procedure_at_bi1` | `text` | Altro tipo di intervento oncoplastico al primo intervento |  |
| `type_of_reconstruction_at_bi1` | `dropdown` | Tipo di ricostruzione al primo intervento | Val: `autocomplete` Opzioni: 1, Espansore tissutale | 2, Protesi | 3, Lembo | 4, Lembo... |
| `other_type_of_immediate_reconstruction_at_bi1` | `text` | Altro tipo di ricostruzione immediata al primo intervento |  |
| `dermal_matrix_at_bi1` | `dropdown` | Matrice dermica al primo intervento | Val: `autocomplete` Opzioni: 0, No | 1, Sintetica | 2, Non sintetica | 9, Ignoto |
| `controlateral_plastic_intervention_at_bi1` | `dropdown` | Intervento di chirurgia plastica controlaterale al primo interven | Val: `autocomplete` Opzioni: 0, No | 1, Rimodellamento | 2, Mastectomia profilattica |... |
| `second_surgical_intervention` | `dropdown` | Esecuzione secondo intervento | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `date_of_bi2` | `text` | Data 2° intervento | Val: `date_ymd`  |
| `bi2` | `dropdown` | Secondo intervento | Val: `autocomplete` Opzioni: 3, Tumorectomia | 4, Resezione ampia | 5, Quadrantectomia... |
| `type_of_mastectomy_at_bi2` | `dropdown` | Tipo di mastectomia al secondo intervento | Val: `autocomplete` Opzioni: 1, Totale | 2, Skin Reducing | 3, Skin Sparing | 4, Nippl... |
| `associated_plastic_surgery_at_bi2` | `dropdown` | Chirurgia plastica associata al secondo intervento | Val: `autocomplete` Opzioni: 0, No | 1, Rimodellamento | 2, Intervento oncoplastico | ... |
| `type_of_oncoplastic_procedure_at_bi2` | `dropdown` | Tipo di intervento oncoplastico al secondo intervento | Val: `autocomplete` Opzioni: 1, Round Block Technique | 2, Mammoplastica orizzontale |... |
| `other_type_of_oncoplastic_procedure_at_bi2` | `text` | Altro tipo di intervento oncoplastico al secondo intervento |  |
| `type_of_reconstruction_at_bi2` | `dropdown` | Tipo di ricostruzione al secondo intervento | Val: `autocomplete` Opzioni: 1, Espansore tissutale | 2, Protesi | 3, Lembo | 4, Lembo... |
| `other_type_of_immediate_reconstruction_at_bi2` | `text` | Altro tipo di ricostruzione immediata al secondo intervento |  |
| `dermal_matrix_at_bi2` | `dropdown` | Matrice dermica al secondo intervento | Val: `autocomplete` Opzioni: 0, No | 1, Sintetica | 2, Non sintetica | 9, Ignoto |
| `intervention_of_controlateral_plastic_surgery_at_bi2` | `dropdown` | Intervento di chirurgia plastica controlaterale al secondo interv | Val: `autocomplete` Opzioni: 0, No | 1, Rimodellamento | 2, Mastectomia profilattica |... |
| `third_surgical_intervention` | `dropdown` | Esecuzione terzo intervento | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `date_of_bi3` | `text` | Data 3° intervento | Val: `date_ymd`  |
| `bi3` | `dropdown` | Terzo intervento | Val: `autocomplete` Opzioni: 4, Resezione ampia | 5, Quadrantectomia/settorectomia | 6... |
| `type_of_mastectomy_at_bi3` | `dropdown` | Tipo di mastectomia al terzo intervento | Val: `autocomplete` Opzioni: 1, Totale | 2, Skin Reducing | 3, Skin Sparing | 4, Nippl... |
| `associated_plastic_surgery_at_bi3` | `dropdown` | Chirurgia plastica associata al terzo intervento | Val: `autocomplete` Opzioni: 0, No | 1, Rimodellamento | 2, Intervento oncoplastico | ... |
| `type_of_oncoplastic_procedure_at_bi3` | `dropdown` | Tipo di intervento oncoplastico al terzo intervento | Val: `autocomplete` Opzioni: 1, Round Block Technique | 2, Mammoplastica orizzontale |... |
| `other_type_of_oncoplastic_procedure_at_bi3` | `text` | Altro tipo di intervento oncoplastico al terzo intervento |  |
| `type_of_reconstruction_at_bi3` | `dropdown` | Tipo di ricostruzione al terzo intervento | Val: `autocomplete` Opzioni: 1, Espansore tissutale | 2, Protesi | 3, Lembo | 4, Lembo... |
| `other_type_of_immediate_reconstruction_at_bi3` | `text` | Altro tipo di ricostruzione immediata al terzo intervento |  |
| `dermal_matrix_at_bi3` | `dropdown` | Matrice dermica al terzo intervento | Val: `autocomplete` Opzioni: 0, No | 1, Sintetica | 2, Non sintetica | 9, Ignoto |
| `intervention_of_controlateral_plastic_surgery_at_bi3` | `dropdown` | Intervento di chirurgia plastica controlaterale al terzo interven | Val: `autocomplete` Opzioni: 0, No | 1, Rimodellamento | 2, Mastectomia profilattica |... |
| `axillary_dissection` | `dropdown` | Esecuzione dissezione ascellare | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `date_of_axillary_dissection` | `text` | Data dissezione ascellare | Val: `date_ymd`  |
| `type_of_dissection` | `dropdown` | Tipo di dissezione | Val: `autocomplete` Opzioni: 0, Sampling | 1, I | 2, I+II | 3, I+II+III | 9, Ignoto |
| `pectoral_muscles` | `dropdown` | Muscoli pettorali | Val: `autocomplete` Opzioni: 1, Conservati entrambi | 2, Asportato piccolo pettorale |... |
| `sentinel_lymph_node_biopsy` | `dropdown` | Esecuzione linfonodo sentinella | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `date_of_sentinel_lymph_node_biopsy` | `text` | Data linfonodo sentinella | Val: `date_ymd`  |
| `blue_dye` | `dropdown` | Esecuzione colorante vitale | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `injection_of_radiotracer` | `dropdown` | Esecuzione isotopo | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `failed_biopsy_prior_to_bi1` | `dropdown` | Biopsia fallita | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `date_of_failed_biopsy_prior_to_bi1` | `text` | Data biopsia fallita | Val: `date_ymd`  |

---

## 📋 Form: `anatomia_patologica` (Anatomia Patologica)
**Scopo Clinico**: Esame istologico definitivo post-operatorio: istotipo invasivo e in situ, grado istologico (Elston-Ellis), pTNM patologico, margini chirurgici, valutazione linfonodale (OSNA/congelatore) e profilo molecolare definitivo.  
**Campi totali**: 50 | **Campi estraibili per l'IA**: 49

| Variabile / Campo | Tipo | Etichetta Clinica | Scelte / Validazione |
|---|---|---|---|
| `text_anatomia_patologica` | `descriptive` | <div class="rich-text-field-label"><p>Nominativo del paziente:    |  |
| `histopathological_diagnosis_of_operative_specimen` | `dropdown` | Diagnosi istopatologica pezzo operatorio | Val: `autocomplete` Opzioni: 0, Benigno | 1, Lesione intraepiteliale | 2, In situ | 3,... |
| `other_diagnosis` | `text` | Altra diagnosi |  |
| `weight_of_the_specimen_gr` | `text` | Peso settore asportato (grammi) | Val: `integer`  |
| `benign` | `dropdown` | Tipo benigno | Opzioni: 1, Tessuto normale | 2, Cisti | 3, Fibroadenoma | 4, Lesi... |
| `intraepithelial_type` | `dropdown` | Tipo intraepiteliale | Opzioni: 1, LIN1 - Iperplasia lobulare atipica | 2, LIN2 - LCIS ti... |
| `grade_of_in_situ_lesion` | `dropdown` | Grado in situ | Val: `autocomplete` Opzioni: 0, Non eseguito | 1, DIN1c - Basso | 2, DIN2 - Intermedio... |
| `in_situ_hysthological_type` | `dropdown` | Tipo in situ | Opzioni: 0, Duttale NOS | 1, Duttale cribriforme | 2, Duttale soli... |
| `presence_of_comedo_necrosis` | `dropdown` | Presenza di necrosi comedonica | Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `classification_used_for_grade` | `dropdown` | Classificazione adottata per il grado in situ | Opzioni: 1, Gruppo Europeo | 2, Holland et al. | 3, Van Nuys | 4, ... |
| `invasive_histological_type` | `dropdown` | Tipo invasivo | Opzioni: 1, Duttale NAS | 2, Lobulare | 3, Tubulare | 4, Cribrifor... |
| `grade` | `dropdown` | Grado | Opzioni: 0, Non eseguito | 1, I | 2, II | 3, III | 4, Non valutabi... |
| `classification_used_for_grading` | `dropdown` | Classificazione adottata per il grado invasivo | Opzioni: 1, OMS | 2, Elston-Ellis | 3, Altro | 9, Ignoto |
| `vascular_invasion` | `dropdown` | Invasione vascolare | Opzioni: 0, Non evidente | 1, Presente NOS | 2, Presente focale | ... |
| `peritumoral_intraductal_component` | `dropdown` | Componente intraduttale peritumorale | Opzioni: 0, Assente | 1, Presente (fino a 25%) | 2, Presente (25%-... |
| `disease_extent` | `dropdown` | Focalità | Opzioni: 1, Unico | 2, Multiplo | 9, Ignoto |
| `size_of_the_lesion_x_mm` | `text` | Dimensione lesione X (mm.) | Val: `integer`  |
| `total_size_invasive_in_situ` | `text` | Dimensioni totali (invasivo+in situ) | Val: `integer`  |
| `pt` | `dropdown` | pT | Opzioni: X, X | 0, 0 | IS, IS | 1, 1 | 1a, 1a | 1b, 1b | 1c, 1c | ... |
| `involved_margins_at_1st_surgical_procedure` | `dropdown` | Margini interessati al 1° intervento | Opzioni: 0, No | 1, A ridosso | 2, Sì | 9, Ignoto |
| `size_of_minimum_free_margin_from_invasive_mm1` | `text` | Distanza minima dal margine dell'invasivo (mm.) al 1° intervento | Val: `integer`  |
| `size_of_minimum_free_margin_from_in_situ_mm1` | `text` | Distanza minima dal margine dell'in situ (mm.) al 1° intervento | Val: `integer`  |
| `involved_margins_at_2nd_surgical_procedure` | `dropdown` | Margini interessati al 2° intervento | Val: `autocomplete` Opzioni: 0, No | 1, A ridosso | 2, Sì | 9, Ignoto |
| `size_of_minimum_free_margin_from_invasive_mm2` | `text` | Distanza minima dal margine dell'invasivo (mm.) al 2° intervento | Val: `integer`  |
| `size_of_minimum_free_margin_from_in_situ_mm2` | `text` | Distanza minima dal margine dell'in situ (mm.) al 2° intervento | Val: `integer`  |
| `positive_axillary_lymphnodes_at_axillary_dissection` | `dropdown` | Metastasi linfonodi ascellari alla dissezione | Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `pn` | `dropdown` | pN | Opzioni: X, X | 0, 0 | 1mi, 1mi | 1a, 1a | 1b, 1b | 1c, 1c | 2a, 2... |
| `excised_lymphnodes_at_axillary_dissection` | `text` | Linfonodi esaminati alla dissezione | Val: `integer`  |
| `positive_lymphnodes_at_axillary_dissection` | `text` | Linfonodi metastatici alla dissezione | Val: `integer`  |
| `positive_post_operative_histology` | `dropdown` | Metastasi al definitivo | Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `pn_lsn` | `dropdown` | pN sentinella | Opzioni: 0, X | 1, 0 (sn) | 2, 0(i-)(sn) | 3, 0(i+)(sn) | 4, 0(mol... |
| `sentinel_lymphnodes_examined` | `text` | Linfonodi sentinella esaminati | Val: `integer`  |
| `sentinel_lymphnode_method` | `dropdown` | Metodica linfonodo sentinella | Val: `autocomplete` Opzioni: 1, OSNA | 2, Congelatore | 9, Non noto |
| `osna_results` | `dropdown` | Esito analisi OSNA | Val: `autocomplete` Opzioni: 0, Negativo (< 160 copie) | 1, Micrometastasi (160 - 2500... |
| `ck19_copies` | `text` | Numero copie mRNA CK19 | Val: `integer`  |
| `other_lymphnodes_examined` | `text` | Altri linfonodi esaminati | Val: `integer`  |
| `positive_sentinel_lymphnodes` | `text` | Linfonodi sentinella metastatici | Val: `integer`  |
| `other_metastatical_lymphnodes` | `text` | Altri linfonodi metastatici | Val: `integer`  |
| `er_immunohistochemical_on_specimen` | `dropdown` | Recettori degli estrogeni post intervento | Opzioni: 0, Non eseguito | 1, Eseguito | 9, Ignoto |
| `er_positive_cells_on_specimen` | `text` | Cellule positive al ER (%) post intervento | Val: `integer`  |
| `er_status_on_specimen` | `dropdown` | Stato ER post intervento | Opzioni: 0, Negativo | 1, Positivo | 9, Ignoto |
| `pr_immunohistochemical_on_specimen` | `dropdown` | Recettori del progesterone post intervento | Opzioni: 0, Non eseguito | 1, Eseguito | 9, Ignoto |
| `pr_positive_cells_on_specimen` | `text` | Cellule positive al PgR (%) post intervento | Val: `integer`  |
| `pr_status_on_specimen` | `dropdown` | Stato PgR post intervento | Val: `autocomplete` Opzioni: 0, Negativo | 1, Positivo | 9, Ignoto |
| `proliferation_activity_ki67_immunohistochemical_on_specimen` | `dropdown` | Attività proliferativa Ki67 post intervento | Val: `autocomplete` Opzioni: 0, Non eseguito | 1, Eseguito | 9, Ignoto |
| `ki67_positive_cells_on_specimen` | `text` | Cellule positive al Ki67 (%) post intervento | Val: `integer`  |
| `her2_overexpression_immunohistochemistry_on_specimen` | `dropdown` | Iperespressione Her2 post intervento | Val: `autocomplete` Opzioni: 0, Non eseguito | 1, Negativo 0/1+ | 2, Dubbio 2+ | 3, Po... |
| `her2_positive_cells_on_specimen` | `text` | Cellule positive al Her2 (%) post intervento | Val: `integer`  |
| `fish_hybridization_on_specimen` | `dropdown` | Stato gene Her2 post intervento | Opzioni: 2, Non eseguito | 0, Non amplificato | 1, Amplificato | 3... |
| `her2_ratio_on_specimen` | `text` | Ratio post intervento | Val: `integer`  |

---

## 📋 Form: `radioterapia` (Radioterapia)
**Scopo Clinico**: Dati del trattamento radioterapico: erogazione, sede e volume irradiato (mammella intera, boost, parete toracica, catene linfonodali), date.  
**Campi totali**: 10 | **Campi estraibili per l'IA**: 9

| Variabile / Campo | Tipo | Etichetta Clinica | Scelte / Validazione |
|---|---|---|---|
| `text_radioterapia` | `descriptive` | <div class="rich-text-field-label"><p>Nominativo del paziente:    |  |
| `radiotherapy_performed` | `dropdown` | Esecuzione radioterapia | Opzioni: 0, No | 1, Sì | 2, No per rifiuto paziente | 9, Ignoto |
| `date_of_first_radiotherapic_cycle` | `text` | Data inizio radioterapia | Val: `date_ymd`  |
| `date_of_last_radiotherapic_cycle` | `text` | Data fine radioterapia | Val: `date_ymd`  |
| `whole_breast` | `dropdown` | Mammella whole breast | Opzioni: 0, No | 1, Cobalto60 | 2, x-MV | 3, Altro | 4, Sì modalit... |
| `partial_breast` | `dropdown` | Mammella parziale | Opzioni: 0, No | 1, Letto operatorio | 2, PBI | 9, Ignoto |
| `chest_wall` | `dropdown` | Parete toracica | Opzioni: 0, No | 1, Cobalto60 | 2, x-MV | 3, Roentgen | 4, Elettro... |
| `supraclavicular_area` | `dropdown` | Regione sovraclaveare | Opzioni: 0, No | 1, Cobalto60 | 2, x-MV | 3, Elettroni | 4, x-MV+E... |
| `internal_mammary_chain` | `dropdown` | Regione mammaria interna | Opzioni: 0, No | 1, Cobalto60 | 2, x-MV | 3, Elettroni | 4, x-MV+E... |
| `rt_on_axilla` | `dropdown` | Regione ascellare | Opzioni: 0, No | 1, Cobalto60 | 2, x-MV | 3, Altro | 9, Ignoto |

---

## 📋 Form: `chemioterapia` (Chemioterapia)
**Scopo Clinico**: Terapie oncologiche mediche: chemioterapia neoadiuvante vs adiuvante, farmaci biologici (trastuzumab), ormonoterapia primaria e adiuvante (tamoxifene, inibitori aromatasi, analoghi GnRH).  
**Campi totali**: 68 | **Campi estraibili per l'IA**: 67

| Variabile / Campo | Tipo | Etichetta Clinica | Scelte / Validazione |
|---|---|---|---|
| `text_chemioterapia` | `descriptive` | <div class="rich-text-field-label"><p>Nominativo del paziente:    |  |
| `prescription_primary_chemotherapy` | `dropdown` | Prescrizione chemioterapia primaria | Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `primary_chemotherapy` | `dropdown` | Chemioterapia primaria | Opzioni: 0, No | 1, Sì | 2, No per rifiuto paziente | 9, Ignoto |
| `date_of_first_primary_chemotherapy_cycle` | `text` | Data primo ciclo CT primaria | Val: `date_ymd`  |
| `drug_1_ct_neoadjuvant` | `dropdown` | Farmaco #1 CT primaria | Opzioni: 1, Antraciclina | 2, Taxani | 3, Antraciclina+Taxani | 9,... |
| `drug_1_commercial_name_ct_neoadjuvant` | `dropdown` | Farmaco #1 (nome commerciale) CT primaria | Opzioni: 1, AC | 2, EC | 3, FEC | 4, CAELYX | 5, TC | 6, TAXOTERE ... |
| `other_drug_1_ct_neoadjuvant` | `text` | Altro farmaco #1 CT primaria |  |
| `no_cycles_drug_1_ct_neoadjuvant` | `text` | N. cicli farmaco #1 CT primaria | Val: `integer`  |
| `drug_2_ct_neoadjuvant` | `dropdown` | Farmaco #2 CT primaria | Val: `autocomplete` Opzioni: 1, Antraciclina | 2, Taxani | 3, Antraciclina+Taxani | 9,... |
| `drug_2_commercial_name_ct_neoadjuvant` | `dropdown` | Farmaco #2 (nome commerciale) CT primaria | Val: `autocomplete` Opzioni: 1, AC | 2, EC | 3, FEC | 4, CAELYX | 5, TC | 6, TAXOTERE ... |
| `other_drug_2_ct_neoadjuvant` | `text` | Altro farmaco #2 CT primaria |  |
| `no_cycles_drug_2_ct_neoadjuvant` | `text` | N. cicli farmaco #2 CT primaria | Val: `integer`  |
| `drug_3_ct_neoadjuvant` | `dropdown` | Farmaco #3 CT primaria | Val: `autocomplete` Opzioni: 1, Antraciclina | 2, Taxani | 3, Antraciclina+Taxani | 9,... |
| `drug_3_commercial_name_ct_neoadjuvant` | `dropdown` | Farmaco #3 (nome commerciale) CT primaria | Val: `autocomplete` Opzioni: 1, AC | 2, EC | 3, FEC | 4, CAELYX | 5, TC | 6, TAXOTERE ... |
| `other_drug_3_ct_neoadjuvant` | `text` | Altro farmaco #3 CT primaria |  |
| `no_cycles_drug_3_ct_neoadjuvant` | `text` | N. cicli farmaco #3 CT primaria | Val: `integer`  |
| `date_of_last_primary_chemotherapy_cycle` | `text` | Data ultimo ciclo CT primaria | Val: `date_ymd`  |
| `withdrawal_due_to_toxicity_ctneo` | `dropdown` | Sospensione per tossicità CT primaria | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `prescription_adjuvant_chemotherapy` | `dropdown` | Prescrizione chemioterapia adiuvante | Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `adjuvant_chemotherapy_administered` | `dropdown` | Esecuzione chemioterapia adiuvante | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 2, No per rifiuto paziente | 9, Ignoto |
| `date_of_first_adjuvant_chemotherapy_cycle` | `text` | Data primo ciclo CT adiuvante | Val: `date_ymd`  |
| `drug_1_ct_adjuvant` | `dropdown` | Farmaco #1 CT adiuvante | Opzioni: 1, Antraciclina | 2, Taxani | 3, Antraciclina+Taxani | 9,... |
| `drug_1_commercial_name_ct_adjuvant` | `dropdown` | Farmaco #1 (nome commerciale) CT adiuvante | Opzioni: 1, AC | 2, EC | 3, FEC | 4, CAELYX | 5, TC | 6, TAXOTERE ... |
| `other_drug_1_ct_adjuvant` | `text` | Altro farmaco #1 CT adiuvante |  |
| `no_cycles_drug_1_ct_adjuvant` | `text` | N. cicli farmaco #1 CT adiuvante | Val: `integer`  |
| `drug_2_ct_adjuvant` | `dropdown` | Farmaco #2 CT adiuvante | Val: `autocomplete` Opzioni: 1, Antraciclina | 2, Taxani | 3, Antraciclina+Taxani | 9,... |
| `drug_2_commercial_name_ct_adjuvant` | `dropdown` | Farmaco #2 (nome commerciale) CT adiuvante | Opzioni: 1, AC | 2, EC | 3, FEC | 4, CAELYX | 5, TC | 6, TAXOTERE ... |
| `other_drug_2_ct_adjuvant` | `text` | Altro farmaco #2 CT adiuvante |  |
| `no_cycles_drug_2_ct_adjuvant` | `text` | N. cicli farmaco #2 CT adiuvante | Val: `integer`  |
| `drug_3_ct_adjuvant` | `dropdown` | Farmaco #3 CT adiuvante | Val: `autocomplete` Opzioni: 1, Antraciclina | 2, Taxani | 3, Antraciclina+Taxani | 9,... |
| `drug_3_commercial_name_ct_adjuvant` | `dropdown` | Farmaco #3 (nome commerciale) CT adiuvante | Val: `autocomplete` Opzioni: 1, AC | 2, EC | 3, FEC | 4, CAELYX | 5, TC | 6, TAXOTERE ... |
| `other_drug_3_ct_adjuvant` | `text` | Altro farmaco #3 CT adiuvante |  |
| `no_cycles_drug_3_ct_adjuvant` | `text` | N. cicli farmaco #3 CT adiuvante | Val: `integer`  |
| `date_of_last_adjuvant_chemotherapy_cycle` | `text` | Data ultimo ciclo CT adiuvante | Val: `date_ymd`  |
| `withdrawal_due_to_toxicity_ctadj` | `dropdown` | Sospensione per tossicità CT adiuvante | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `prescription_of_primary_therapy_with_biological_drugs` | `dropdown` | Prescrizione terapia primaria con farmaci biologici | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `primary_therapy_with_biological_drugs` | `dropdown` | Terapia primaria con farmaci biologici | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 2, No per rifiuto paziente | 9, Ignoto |
| `drug_bio_neoadj` | `dropdown` | Farmaco terapia primaria | Opzioni: 1, Trastuzumab | 2, Altro | 9, Ignoto |
| `other_drug_bio_neoadj` | `text` | Altro farmaco terapia primaria |  |
| `number_of_cycles_bio_neoadj` | `text` | Numero somministrazioni terapia primaria | Val: `integer`  |
| `date_of_first_administration_of_biological_drugs_for_primary_therapy` | `text` | Data inizio terapia primaria | Val: `date_ymd`  |
| `date_of_last_administration_of_biological_drugs_for_primary_therapy` | `text` | Data fine terapia primaria | Val: `date_ymd`  |
| `withdrawal_due_to_herceptin_toxicity_neoadj` | `dropdown` | Sospensione per tossicità Herceptin terapia primaria | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `prescription_of_adjuvant_therapy_with_biological_drugs` | `dropdown` | Prescrizione terapia adiuvante con farmaci biologici | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `adjuvant_therapy_with_biological_drugs` | `dropdown` | Terapia adiuvante con farmaci biologici | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 2, No per rifiuto paziente | 9, Ignoto |
| `drug_bio_adj` | `dropdown` | Farmaco terapia adiuvante | Opzioni: 1, Trastuzumab | 2, Altro | 9, Ignoto |
| `other_drug_bio_adj` | `text` | Altro farmaco terapia adiuvante |  |
| `number_of_cycles_bio_adj` | `text` | Numero somministrazioni terapia adiuvante | Val: `integer`  |
| `date_of_first_administration_of_biological_drugs_for_adjuvant_therapy` | `text` | Data inizio terapia adiuvante | Val: `date_ymd`  |
| `date_of_last_administration_of_biological_drugs_for_adjuvant_therapy` | `text` | Data fine terapia adiuvante | Val: `date_ymd`  |
| `withdrawal_due_to_herceptin_toxicity_adj` | `dropdown` | Sospensione per tossicità Herceptin terapia adiuvante | Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `prescription_of_primary_endocrine_therapy` | `dropdown` | Prescrizione trattamento ormonale primario | Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `primary_endocrine_therapy` | `dropdown` | Trattamento ormonale primario | Opzioni: 0, No | 1, Sì | 2, No per rifiuto paziente | 9, Ignoto |
| `ovarian_ablation_with_gnrh_analogs` | `dropdown` | Ablazione ovarica con GnRH analoghi ormonoterapia primaria | Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `date_of_first_gnrh_analogs_administration_for_primary_therapy` | `text` | Data inizio GnRH analoghi ormonoterapia primaria | Val: `date_ymd`  |
| `tamoxifen_neoadj` | `dropdown` | Tamoxifene ormonoterapia primaria | Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `date_of_first_tamoxifen_administration_for_primary_therapy` | `text` | Data inizio Tamoxifene ormonoterapia primaria | Val: `date_ymd`  |
| `date_of_last_tamoxifen_administration_for_primary_therapy` | `text` | Data fine Tamoxifene ormonoterapia primaria | Val: `date_ymd`  |
| `aromatase_inhibitors_ai` | `dropdown` | Inibitori della Aromatasi (I.A.) ormonoterapia primaria | Opzioni: 0, No | 1, Letrozolo | 2, Anastrazolo | 3, Exemestane | 4... |
| `date_of_first_ai_administration_for_primary_therapy` | `text` | Data inizio I.A. ormonoterapia primaria | Val: `date_ymd`  |
| `prescription_adjuvant_endocrine_therapy` | `dropdown` | Prescrizione trattamento ormonale adiuvante | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `adjuvant_endocrine_therapy` | `dropdown` | Trattamento ormonale adiuvante | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 2, No per rifiuto paziente | 9, Ignoto |
| `date_of_first_gnrh_analogs_administration_for_adjuvant_therapy` | `text` | Data inizio GnRH analoghi ormonoterapia adiuvante | Val: `date_ymd`  |
| `tamoxifen_adj` | `dropdown` | Tamoxifene ormonoterapia adiuvante | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `date_of_first_tamoxifen_administration_for_adjuvant_therapy` | `text` | Data inizio Tamoxifene ormonoterapia adiuvante | Val: `date_ymd`  |
| `date_of_last_tamoxifen_administration_for_adjuvant_therapy` | `text` | Data fine Tamoxifene ormonoterapia adiuvante | Val: `date_ymd`  |
| `aromatase_inhibitor_ai` | `dropdown` | Inibitori della Aromatasi (I.A.) ormonoterapia adiuvante | Val: `autocomplete` Opzioni: 0, No | 1, Letrozolo | 2, Anastrazolo | 3, Exemestane | 4... |
| `date_of_first_ai_administration_for_adjuvant_therapy` | `text` | Data inizio I.A. ormonoterapia adiuvante | Val: `date_ymd`  |

---

## 📋 Form: `recidiva` (Recidiva)
**Scopo Clinico**: Monitoraggio recidive di malattia: recidiva locale mammaria, ascellare omolaterale, catena mammaria interna, sovraclaveare e parete toracica.  
**Campi totali**: 11 | **Campi estraibili per l'IA**: 10

| Variabile / Campo | Tipo | Etichetta Clinica | Scelte / Validazione |
|---|---|---|---|
| `text_recidiva` | `descriptive` | <div class="rich-text-field-label"><p>Nominativo del paziente:    |  |
| `breast_local_recurrence` | `dropdown` | Recidiva locale mammella residua | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `date_of_breast_local_recurrence` | `text` | Data recidiva locale mammella residua | Val: `date_ymd`  |
| `ipsilateral_axillary_recurrence` | `dropdown` | Recidiva sull'ascella omolaterale | Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `date_of_ipsilateral_axillary_recurrence` | `text` | Data recidiva sull'ascella omolaterale | Val: `date_ymd`  |
| `internal_mammary_chain_recurrence` | `dropdown` | Recidiva mammaria interna | Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `date_internal_mammary_chain_recurrence` | `text` | Data recidiva mammaria interna | Val: `date_ymd`  |
| `ipsilateral_supraclavicular_recurrence` | `dropdown` | Recidiva sovraclaveare omolaterale | Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `date_of_ipsilateral_supraclavicular_recurrence` | `text` | Data recidiva sovraclaveare omolaterale | Val: `date_ymd`  |
| `thoracic_wall_recurrence` | `dropdown` | Recidiva sulla parete toracica | Val: `autocomplete` Opzioni: 0, No | 1, Sì | 9, Ignoto |
| `date_of_thoracic_wall_recurrence` | `text` | Data recidiva sulla parete toracica | Val: `date_ymd`  |

---
