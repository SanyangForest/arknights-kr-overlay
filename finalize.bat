@echo off
rem 번역 결과 검증 → 이름 표기 통일 → 사전 재생성 → 기록 화면 재점검
rem 결과: chunks\*.rejects.tsv (거부된 번역), out\report.md (사전 통계), overlay\captures\*_eval\report.md (화면 점검)
cd /d "%~dp0"
python -E -s merge_translations.py
python -E -s unify_names.py
python -E -s build_dict.py "%~dp0data\gamedata" > nul
python -E -s build_recruit.py "%~dp0data\gamedata"
"%~dp0.venv\Scripts\python.exe" build_story.py "%~dp0data\gamedata"
type out\report.md
cd overlay
for %%s in (session1 session2 session4) do (
  if exist captures\%%s "%~dp0.venv\Scripts\python.exe" batch_eval.py ..\out\dict_zh_ko.json captures\%%s
)
pause
