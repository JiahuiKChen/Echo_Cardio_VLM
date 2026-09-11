"""Synthetic-only finalization regression tests; no SCC data or image pixels."""
import json
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from jdim_tier1 import audit_finalization as final
from jdim_tier1 import audit_protocol_v3 as v3
from jdim_tier1.reduced_audit_interface import RoleAwareAuditService
from jdim_tier1.safety import Tier1BlockedError
from test_jdim_reduced_audit import ParentFixture, complete_v3_payload


class FinalAuditLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        fixture = ParentFixture(self.root)
        self.package = self.root / "reduced"
        fixture.build_package(self.package)
        v3.apply_protocol_v3_transition(v3.plan_protocol_v3_transition(self.package), source_commit="a"*40)
        self.service = RoleAwareAuditService(self.package, fixture.paths.parent_media_root)
        for code in ("SYNTHETICPRIMARY", "SYNTHETICSECONDARY"):
            self.service.registry.register(code, qualified=True)
        for role, code, count in (("primary", "SYNTHETICPRIMARY", 30), ("secondary", "SYNTHETICSECONDARY", 2)):
            for _ in range(count):
                claimed = self.service.claim({"reviewer_code": code, "role": role, "action": "claim_next", "qualification_confirmed": True})
                payload = complete_v3_payload(claimed["study"])
                for record in payload["annotations"]["clips"].values():
                    for field in v3.V3_CLIP_PRESENCE_FIELDS:
                        record[field] = "no"
                    record["acquisition_content_type"] = "2d_b_mode"
                    record["reader_confidence"] = "high"
                    if v3.V3_SOURCE_ONLY_PRIMARY_FIELD in record:
                        record[v3.V3_SOURCE_ONLY_PRIMARY_FIELD] = "no"
                summary = v3.derive_study_summary(payload["annotations"]["clips"],self.service.checkpoints.clip_evidence_tiers)
                summary.update(derived_summary_confirmed="yes",reader_confidence="high")
                payload["annotations"]["studies"] = {claimed["study"]["audit_id"]:summary}
                self.service.save(claimed["session_token"], payload["annotations"])
                self.service.lock(claimed["session_token"])
        self.output = self.package / "restricted/finalization/final_v1"

    def tearDown(self):
        self.temp.cleanup()

    def freeze(self):
        with patch.object(final, "owner_access", return_value={"status": "OWNER_FINALIZATION_ACCESS_READY"}):
            return final.freeze(self.package, self.output, review_source_commit="a"*40,
                tool_commit="b"*40, expected_user="synthetic", confirmation=final.CONFIRM,
                review_service_stopped=True)

    def test_complete_pre_preset_reviews_are_valid_and_no_state_changes(self):
        before = final.input_inventory(self.package)
        data = final.validate(self.package, "a"*40)["safe"]
        self.assertEqual(data["primary_reviews"], 30)
        self.assertEqual(data["valid_repeat_pairs"], 2)
        self.assertEqual(data["counts"]["pre_preset_reviews"], 32)
        self.assertEqual(before, final.input_inventory(self.package))

    def test_backup_lock_and_noop_adjudication(self):
        before = final.input_inventory(self.package)
        self.freeze()
        self.assertEqual(before, final.input_inventory(self.package))
        self.assertEqual(final.verify_lock(self.output)["status"], "BLINDED_AUDIT_ANNOTATIONS_LOCKED")
        amendment = final.load(self.output / "repeat_feasibility_amendment.json")
        self.assertEqual(amendment["original_intended_repeat_studies"], 8)
        self.assertFalse(amendment["formal_reliability_coefficient_estimated"])
        self.assertEqual(final.make_queue(self.output)["status"], "BLINDED_ADJUDICATION_NOT_REQUIRED")

    def test_existing_lock_never_overwritten(self):
        self.freeze()
        with self.assertRaises(ValueError):
            self.freeze()

    def test_bad_lock_hash_fails_closed(self):
        self.freeze()
        p = self.output / "blinded_snapshot_restricted.json"
        p.chmod(0o600)
        p.write_text("{}")
        with self.assertRaises(ValueError):
            final.verify_lock(self.output)

    def test_finalized_checkpoint_tampering_fails(self):
        q = final.load(self.service.queue.state_path)
        p = self.service.checkpoints.root / q["events"][0]["event_id"] / "checkpoint.json"
        p.chmod(0o600)
        p.write_text(p.read_text() + " ")
        with self.assertRaises((ValueError, Tier1BlockedError)):
            final.validate(self.package, "a"*40)

    def test_no_primary_missing_or_replaced(self):
        q = final.load(self.service.queue.state_path)
        q["events"].pop(0)
        self.service.queue.state_path.write_text(json.dumps(q))
        with self.assertRaises(ValueError):
            final.validate(self.package, "a"*40)

    def test_confirmation_required(self):
        with patch.object(final, "owner_access", return_value={}):
            with self.assertRaises(ValueError):
                final.freeze(self.package, self.output, review_source_commit="a"*40,
                    tool_commit="b"*40, expected_user="synthetic", confirmation="no",
                    review_service_stopped=True)

    def test_backup_precedes_annotation_validation_and_survives_failure(self):
        with patch.object(final,"validate",side_effect=ValueError("synthetic integrity failure")):
            with self.assertRaises(ValueError):
                self.freeze()
        self.assertTrue((self.output / "final_review_state.tar").is_file())
        self.assertTrue((self.output / "backup_certificate.json").is_file())
        self.assertFalse((self.output / "primary_completion_certificate.json").exists())
        self.assertTrue(final.load(self.output / "blocked_validation_certificate.json")["original_state_unchanged"])

    def test_empty_queue_export_is_aggregate_only(self):
        from jdim_tier1.audit_final_export import export_no_adjudication
        self.freeze()
        final.make_queue(self.output)
        result = export_no_adjudication(self.output)
        self.assertEqual(result["status"],"MANUAL_INPUT_CONTENT_AUDIT_LOCKED")
        payload = final.load(self.output / "aggregate_safe/manual_input_content_audit_summary.json")
        text = json.dumps(payload)
        for forbidden in ("reviewer_code", "audit_id", "subject_id", "study_id", "DICOM", "candidate_target_value_text"):
            self.assertNotIn(forbidden,text)

    def test_export_requires_noop_gate(self):
        from jdim_tier1.audit_final_export import export_no_adjudication
        self.freeze()
        with self.assertRaises(FileNotFoundError):
            export_no_adjudication(self.output)

    def test_queue_blinding_and_human_checkpoint_immutability(self):
        from jdim_tier1.audit_adjudication import AdjudicationStore, lock_adjudication
        self.freeze()
        with patch.object(final,"adjudication_reasons",return_value={"calipers":["synthetic_test"]}):
            final.make_queue(self.output)
        queue = final.load(self.output / "adjudication_queue_restricted.json")
        for task in queue["tasks"]:
            self.assertEqual(set(task),{"token","evidence_tier","fields","reasons","primary","secondary"})
        store = AdjudicationStore(self.output,self.root)
        task = store.next_task()
        with self.assertRaises(ValueError): store.save(task["token"],{"calipers":"no"},"SYNTHETICPRIMARY",False)
        with self.assertRaises(PermissionError): store.save(task["token"],{"calipers":"no"},"UNREGISTERED",True)
        store.save(task["token"],{"calipers":"no"},"SYNTHETICPRIMARY",True)
        with self.assertRaises(FileExistsError): store.save(task["token"],{"calipers":"yes"},"SYNTHETICPRIMARY",True)
        self.assertEqual(store.progress()["completed"],1)
        with self.assertRaises(ValueError): lock_adjudication(self.output)

    def test_http_blinding_host_origin_and_auth_gates(self):
        from jdim_tier1 import audit_adjudication as adj
        self.freeze()
        final.make_queue(self.output)
        seen = []
        class FakeServer:
            def __init__(inner,address,handler):
                self.assertEqual(address,("127.0.0.1",8767))
                inner.handler = handler
            def serve_forever(inner):
                handler = object.__new__(inner.handler)
                handler.reply = lambda status,payload,*args,**kwargs: seen.append((status,payload))
                handler.path = "/health"
                handler.headers = {"Host":"evil.example"}
                handler.do_GET()
                self.assertEqual(seen[-1][0],403)
                handler.headers = {"Host":"127.0.0.1:8767"}
                handler.do_GET()
                self.assertEqual(seen[-1][0],200)
                handler.path = "/api/next"
                handler.do_GET()
                self.assertEqual(seen[-1][0],403)
                handler.path = "/api/login"
                body = json.dumps({"reviewer":"SYNTHETICPRIMARY","qualified":True}).encode()
                handler.headers.update({"Content-Length":str(len(body)),"Origin":"http://evil.example"})
                handler.rfile = io.BytesIO(body)
                handler.do_POST()
                self.assertEqual(seen[-1][0],403)
                handler.headers["Origin"] = "http://127.0.0.1:8767"
                handler.rfile = io.BytesIO(body)
                handler.do_POST()
                self.assertEqual(seen[-1][0],200)
                self.assertEqual(set(seen[-1][1]),{"csrf"})
            def server_close(inner): pass
        with patch.object(adj,"ThreadingHTTPServer",FakeServer),patch("builtins.print"):
            adj.serve(self.output,self.root,8767)


class AdjudicationEligibilityTests(unittest.TestCase):
    def test_generic_positive_fields_do_not_trigger_queue(self):
        p = {"visible_text":"yes","visible_numeric_value":"yes","source_only_visible_text":"yes",
             "source_only_numeric_value":"yes","source_only_unit":"yes","source_only_relevant_content":"yes",
             "acquisition_content_type":"color_doppler","reader_confidence":"high"}
        self.assertEqual(final.adjudication_fields(p,None),[])

    def test_generic_uncertainty_or_disagreement_does_trigger(self):
        self.assertEqual(final.adjudication_fields({"visible_text":"uncertain"},None),["visible_text"])
        self.assertEqual(final.adjudication_fields({"visible_numeric_value":"yes"},{"visible_numeric_value":"no"}),["visible_numeric_value"])

    def test_measurement_modalities_and_not_assessable_trigger(self):
        for value in ("m_mode","pulsed_wave_spectral_doppler","continuous_wave_spectral_doppler","tissue_doppler","not_assessable"):
            self.assertIn("acquisition_content_type",final.adjudication_fields({"acquisition_content_type":value},None))

    def test_entered_candidate_value_triggers_even_without_positive_flag(self):
        self.assertIn("candidate_target_value",final.adjudication_fields({"candidate_target_value_present":"no","candidate_target_value":"12"},None))

    def test_blinding_fails_on_nested_forbidden_keys(self):
        with self.assertRaises(ValueError):
            final.require_blinded_keys({"tasks":[{"target_membership":"synthetic"}]})
        final.require_blinded_keys({"tasks":[{"fields":["lvot_vti_specific_label"]}]})

    def test_adjudication_requires_complete_human_choices(self):
        from jdim_tier1.audit_adjudication import decision_values
        task = {"fields":["visible_text","calipers"]}
        with self.assertRaises(ValueError): decision_values(task,{"visible_text":"yes"})
        with self.assertRaises(ValueError): decision_values(task,{"visible_text":"yes","calipers":""})
        self.assertEqual(decision_values(task,{"visible_text":"yes","calipers":"uncertain"})["calipers"],"uncertain")

    def test_exact_proportion_interval(self):
        from jdim_tier1.audit_final_export import exact_interval
        self.assertEqual(exact_interval(0,15)[0],0)
        self.assertAlmostEqual(exact_interval(0,15)[1],0.21801936091,places=8)
        with self.assertRaises(ValueError): exact_interval(1,0)
    def test_positive_uncertain_and_disagreement(self):
        primary = {"calipers": "yes", "visible_text": "uncertain", "tapse_specific_label": "no"}
        secondary = {**primary, "tapse_specific_label": "yes"}
        self.assertEqual(final.adjudication_fields(primary, secondary), ["calipers", "tapse_specific_label", "visible_text"])

    def test_candidate_detail_and_source_only_inclusion(self):
        fields = final.adjudication_fields({"candidate_target_value_present": "yes", "source_only_candidate_target_value": "yes"}, None)
        self.assertIn("candidate_target_value", fields)
        self.assertIn("source_only_candidate_target_value_text", fields)

    def test_notes_and_confidence_never_become_decision_fields(self):
        self.assertEqual(final.adjudication_fields({"restricted_notes": "uncertain", "reader_confidence": "uncertain"}, None), [])

    def test_protocol_presence_is_not_interpreted(self):
        self.assertEqual(final.adjudication_fields({"calipers": "no"}, None), [])

    def test_exclusive_admin_write(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "admin.json"
            final.write_once(p, {"action": "synthetic"})
            with self.assertRaises(FileExistsError):
                final.write_once(p, {})


if __name__ == "__main__":
    unittest.main()
