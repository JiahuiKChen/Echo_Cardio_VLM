"""Local synthetic aggregate protocol tests; no project patients or SCC access."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO/'scripts'), str(REPO/'tests')]
spec = importlib.util.spec_from_file_location('transport', str(Path(__file__).with_name('reporting_transport.py')))
t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t); t.initialize(REPO)
import test_lvef_revalidation_analysis as et
import test_render_lvef_revalidation_results as rt
a, inf, r = t.engine, t.inference, t.renderer


def paired(x):
    return inf.paired_summary(x, np.full(10000, x))


def model(x):
    return inf.model_interval(x if x is not None else np.nan, np.full(10000, x if x is not None else np.nan))


def simple(values):
    return dict(contrasts={f'{l}_minus_{rr}': paired(values[l]-values[rr]) for l,rr in inf.CONTRASTS},
        one_class_replicates=0, one_class_frequency=0., observed=values, replicates=10000, shared_subject_multiplicities=True)


def collect(values, binary=False):
    keys=set(values[a.MODALITIES[0]])
    output=dict(contrasts={f'{l}_minus_{rr}': {k: paired(values[l][k]-values[rr][k]) for k in keys} for l,rr in inf.CONTRASTS},
        model_metrics={m:{k:model(v) for k,v in metrics.items()} for m,metrics in values.items()},
        replicates=10000, shared_subject_multiplicities=True, effect_orientation='named_left_modality_minus_named_right_modality',
        metric_directions={k:inf.metric_orientation(k) for k in keys}, scope='secondary_effects_and_intervals_no_additional_superiority_claim')
    if binary:output.update(one_class_replicates=0,one_class_frequency=0.)
    return output


@pytest.fixture(scope='module')
def payload():
    evaluation, _, audit = rt.aggregate_sources.__wrapped__(et.fitted.__wrapped__())
    from build_target_dependency_registry import STRICT_TARGETS
    # Clone aggregate-only task states to the actual 21-task scale for file-size
    # observation. This is a synthetic protocol fixture, never scientific output.
    names=[n for n in STRICT_TARGETS if n not in ('lvef','tr_mmhg','mitral_e_velocity')]
    names=names[:21]
    while len(names)<21:names.append('synthetic_task_'+str(len(names)))
    original=copy.deepcopy(evaluation['targets']['lvot_vti'])
    evaluation['strict_panel']=names
    evaluation['targets']={'lvef':evaluation['targets']['lvef'],**{n:copy.deepcopy(original) for n in names}}
    lock='a'*64
    er=dict(status='PASS_LOCKED_TEST_EVALUATION',analysis_lock_sha256=lock,input_sha256=a.digest(audit),spec_sha256=evaluation['spec_sha256'],
        frozen_sha256=evaluation['frozen_sha256'],release_sha256='d'*64,predictions_sha256=a.digest(evaluation),test_loader_invocations=1)
    report=dict(status='PASS_PRIVATE_PAIRED_REPORT',evaluation_receipt_sha256=a.digest(er),analysis_lock_sha256=lock,
        spec_sha256=evaluation['spec_sha256'],conditions={},patient_level_outputs_exported=False,public_export_approved=False)
    base={'prevalence','auroc','average_precision','sensitivity','specificity','ppv','npv','f1','balanced_accuracy'}
    for condition in a.EVALUATION_CONDITIONS:
        panel=inf.summarize_panel(evaluation,condition=condition)
        targets={}
        for target,row in evaluation['targets'].items():
            states=dict(row['conditions'][condition])
            if condition=='no_indicators':states['vision_only']=row['conditions']['primary']['vision_only']
            values={m:inf._numeric_metrics(states[m]['metrics']) for m in a.MODALITIES}
            targets[target]={'continuous':collect(values)}
            if target=='lvef':
                targets[target]['binary']={}
                for endpoint in a.ENDPOINTS:
                    values={}
                    for m in a.MODALITIES:
                        bm=inf._numeric_metrics(states[m]['binary'][endpoint]['metrics'])
                        bm.update({prefix+k:bm[k] for prefix in ('fixed_0_5_','coherence_') for k in base})
                        values[m]=bm
                    targets[target]['binary'][endpoint]=collect(values,binary=True)
        states=evaluation['targets']['lvef']['conditions'][condition]
        if condition=='no_indicators':states={**states,'vision_only':evaluation['targets']['lvef']['conditions']['primary']['vision_only']}
        anchor=simple({m:states[m]['metrics']['mae'] for m in a.MODALITIES})
        binary=simple({m:states[m]['binary']['lvef_lt_40']['metrics']['auroc'] for m in a.MODALITIES})
        macro_values={m:panel[m]['macro_normalized_mae'] for m in a.MODALITIES}
        macro=dict(task_native_mae_contrasts={n:targets[n]['continuous']['contrasts'] for n in names},
            macro_normalized_mae=macro_values,macro_contrasts=simple(macro_values)['contrasts'],locked_task_count=len(names),roster_subjects=80,
            effect_orientation='named_left_modality_minus_named_right_modality',metric_direction='lower_is_better',
            shared_subject_multiplicities=True,undefined_task_invalidates_macro=True)
        macro['task_native_mae_contrasts']={n:{k:v['mae'] for k,v in targets[n]['continuous']['contrasts'].items()} for n in names}
        p={}
        for i,k in enumerate(t.ORDERED_CONTRASTS[:2]):
            p[inf.CORE_CLAIMS[i]]=anchor['contrasts'][k]['p_value'];p[inf.CORE_CLAIMS[i+2]]=macro['macro_contrasts'][k]['p_value']
        primary=dict(replicates=10000,seed=a.SEED,lvef_mae=anchor,strict_panel=macro,condition=condition,endpoint='lvef_lt_40',
            core_multiplicity=inf.core_holm(p,strict_panel=names,strict_panel_locked=True) if condition=='primary' else {'status':'SECONDARY_NO_CORE_CLAIM'},
            secondary_logistic_auroc=binary,secondary_binary_holm=inf.holm({k:binary['contrasts'][k]['p_value'] for k in t.ORDERED_CONTRASTS[:2]}) if condition=='primary' else {'status':'DESCRIPTIVE_NO_ADDITIONAL_HOLM_FAMILY'},
            binary_family_role='secondary_not_core_global_fwer',inference_scope='conditional_on_fixed_selected_models')
        report['conditions'][condition]=dict(primary_inference=primary,panel_summary=panel,secondary_intervals=dict(
            replicates=10000,seed=a.SEED,condition=condition,targets=targets,shared_panel_draws_across_tasks=True,fixed_models=True))
    candidate=r.extract_aggregate_bundle(evaluation,report,input_audit=audit)
    safety=dict(status='PASS_REVALIDATION_AGGREGATE_SAFETY',candidate_sha256=a.digest(candidate),analysis_lock_sha256=lock,
        input_sha256=a.digest(audit),evaluation_receipt_sha256=a.digest(er),report_sha256=a.digest(report),
        checks=['CLOSED_COMPLETE_AGGREGATE_SCHEMA','MAINTAINED_AGGREGATE_SAFE_JSON'],validator_sha256=dict(renderer='a'*64,audit_utils='b'*64,safety_policy='c'*64),
        patient_level_outputs_exported=False,public_export_approved=False)
    bundle=r.seal_aggregate_bundle(candidate,dict(status=safety['status'],candidate_sha256=a.digest(candidate),safety_receipt_sha256=a.digest(safety)))
    return dict(report=report,candidate=candidate,safety=safety,bundle=bundle,evaluation=er)


def test_realistic_shape_load_and_size(payload,tmp_path):
    tmp_path.chmod(0o700)
    names={'report':'paired_report','safety':'aggregate_safety','bundle':'aggregate_bundle','evaluation':'test_evaluation'}
    for key,name in names.items():
        path=tmp_path/(name+'.restricted.json');path.write_bytes(a.canonical_bytes(payload[key]));path.chmod(0o600)
    out=t.load(tmp_path,a.digest(payload['bundle']))
    assert set(out)=={'asa_supplement','full_paired_report','bundle','aggregate_safety'}
    assert out['full_paired_report']==a.canonical_bytes(payload['report'])
    print('SYNTHETIC_21_TASK_BYTES',json.dumps({k:len(v) for k,v in out.items()}))
    broken=tmp_path/'paired_report.restricted.json';broken.write_bytes(broken.read_bytes()+b' ')
    with pytest.raises(ValueError,match='PAIRED_REPORT_HASH_CHANGED'):t.load(tmp_path,a.digest(payload['bundle']))


@pytest.mark.parametrize('change',['macro','core','binary','draws','condition','identifier'])
def test_semantic_mutations_are_refused(payload,change):
    report=copy.deepcopy(payload['report']);candidate=payload['candidate']
    primary=report['conditions']['primary']['primary_inference']
    if change=='macro':primary['strict_panel']['locked_task_count']-=1
    elif change=='core':primary['core_multiplicity']['adjusted_p_values'][inf.CORE_CLAIMS[0]]=.723
    elif change=='binary':primary['secondary_binary_holm'][t.ORDERED_CONTRASTS[0]]=.723
    elif change=='draws':report['conditions']['random_10']['secondary_intervals']['replicates']=999
    elif change=='condition':report['conditions']['no_indicators']['primary_inference']['core_multiplicity']=primary['core_multiplicity']
    else:report['subject_ids']=['SYNTHETIC_PRIVATE']
    with pytest.raises((ValueError,a.AnalysisError)):t.validate_report(report,candidate)


def write_validated_controls(payload, root):
    root.mkdir(mode=0o700)
    names={'report':'paired_report','safety':'aggregate_safety','bundle':'aggregate_bundle','evaluation':'test_evaluation'}
    for key,name in names.items():
        path=root/(name+'.restricted.json');path.write_bytes(a.canonical_bytes(payload[key]));path.chmod(0o600)
    return t.load(root,a.digest(payload['bundle']))


@pytest.mark.parametrize('codec',['gzip','xz'])
def test_pack_once_fast_reads_exact_byte_round_trip_and_default_scope(payload,tmp_path,codec,capsys):
    payloads=write_validated_controls(payload,tmp_path/'controls')
    directory=tmp_path/'packet'
    manifest=t.write_pack(directory,payloads,codec=codec)
    expected=t.sha(t.canonical(manifest))
    assert set(manifest['payload_sha256'])=={'bundle','asa_supplement'}
    assert manifest['codec']==codec+'_base64' and manifest['chunk_count']<=512
    assert all(path.stat().st_mode & 0o777==0o600 for path in directory.iterdir())
    observed,encoded=t.read_pack(directory,expected)
    chunks=[t.pack_chunks(observed,encoded,i,1)[0] for i in range(manifest['chunk_count'])]
    out=t.decode_pack(manifest,list(reversed(chunks)),expected)
    assert out=={key:payloads[key] for key in ('bundle','asa_supplement')}
    with patch.object(t,'initialize',side_effect=AssertionError('must not reinitialize science')), \
         patch.object(t,'load',side_effect=AssertionError('must not revalidate original source')), \
         patch.object(sys,'argv',['transport','--pack-read',str(directory),'--expected-pack-manifest-sha256',expected]):
        assert t.main()==0
    assert json.loads(capsys.readouterr().out)==chunks[0]
    with pytest.raises(FileExistsError):t.write_pack(directory,payloads,codec=codec)
    assert t.read_pack(directory,expected)==(observed,encoded)
    print('SYNTHETIC_PACK',codec,manifest['packet_bytes'],manifest['compressed_bytes'],manifest['chunk_count'])


@pytest.mark.parametrize('mutation',['missing','duplicate','body','chunk_hash','manifest_hash','compressed','range'])
def test_transport_corruption_or_scope_expansion_is_refused(payload,tmp_path,mutation):
    payloads=write_validated_controls(payload,tmp_path/'controls')
    directory=tmp_path/'packet'
    manifest=t.write_pack(directory,payloads,include_full_report=True,chunk_characters=16384)
    expected=t.sha(t.canonical(manifest))
    assert set(manifest['payload_sha256'])=={'bundle','asa_supplement','full_paired_report'}
    observed,encoded=t.read_pack(directory,expected)
    chunks=[t.pack_chunks(observed,encoded,i,1)[0] for i in range(manifest['chunk_count'])]
    if mutation=='missing':chunks.pop()
    elif mutation=='duplicate':chunks.append(chunks[0])
    elif mutation=='body':chunks[0]['compressed_base64']='A'+chunks[0]['compressed_base64'][1:]
    elif mutation=='chunk_hash':chunks[0]['chunk_sha256']='a'*64
    elif mutation=='manifest_hash':expected='a'*64
    elif mutation=='compressed':
        path=directory/'packet.compressed';path.write_bytes(path.read_bytes()+b'!')
        with pytest.raises(ValueError,match='PACK_GZIP_CHANGED'):t.read_pack(directory,expected)
        return
    else:
        with pytest.raises(ValueError,match='PACK_CHUNK_RANGE_INVALID'):t.pack_chunks(observed,encoded,0,513)
        return
    with pytest.raises(ValueError):t.decode_pack(manifest,chunks,expected)
