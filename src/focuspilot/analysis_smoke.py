"""One opt-in paid-model analysis with fictional notifications/database only."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading

from .analysis_engine import AnalysisEngine
from .storage import TaskStore
from .sandbox_runtime_probe import digest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approve-model-analysis',action='store_true')
    args=parser.parse_args()
    if not args.approve_model_analysis:
        parser.error('Requires --approve-model-analysis; no credentials read or model called')
    project=Path(__file__).resolve().parents[2]
    (project/'.tmp').mkdir(exist_ok=True)
    engine=AnalysisEngine()
    report={'production_data_used':False,'formal_database_written':False}
    try:
        with TemporaryDirectory(prefix='model-analysis-',dir=project/'.tmp') as directory:
            path=Path(directory)/'fixture.sqlite3';store=TaskStore(path)
            store.create('提交周报','2026-10-25T17:00:00')
            before=digest(path)
            result=engine.analyze('明天17点提交周报，高优先级，预计30分钟。整理项目资料，无截止日期，预计45分钟。',
                path,threading.Event(),lambda message:print(message,flush=True))
            report.update(engine.last_report)
            report['database_unchanged']=digest(path)==before
            report['duplicate_candidate_count']=len(result['duplicates'])
            report['draft_titles']=[item['title'] for item in result['tasks']]
            report['passed']=len(result['tasks'])==2 and report['database_unchanged'] and bool(result['duplicates'])
            if not report['passed']:raise ValueError('Fictional analysis acceptance failed')
    except Exception as error:
        report.update(engine.last_report)
        report['passed']=False
        report['error_type']=type(error).__name__
        report['error']=str(error)[:300]
    finally:
        engine.close()
        report['runtime_cleaned']=engine.box is None
    (project/'docs/T08_MODEL_ANALYSIS_VALIDATION.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if not report.get('passed'):raise SystemExit(1)


if __name__=='__main__':main()
