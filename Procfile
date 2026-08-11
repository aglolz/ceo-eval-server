# Pick ONE entry point per Railway deployment:
#
# --workers 2 --threads 8: --threads switches gunicorn to the gthread worker,
# so up to 16 requests are in flight at once. The old config was ONE sync
# worker — while it sat in a minutes-long scoring request, every other webhook
# (including assistant-request, which Vapi needs answered in 7.5s to route the
# A/B arm) queued until Vapi reset the connection. Scoring itself now runs
# off-request in ceo_live_server.SCORING_EXECUTOR, so these threads only ever
# serve quick requests; --timeout 120 stays as a safety net.
#
# CEO Live (PRODUCTION — the live number routes here; all judges):
# web: gunicorn ceo_live_server:app --bind 0.0.0.0:$PORT --workers 2 --threads 8 --timeout 120
#
# Maya's Test (all 10 judges; still scores in-request — see
# WEBHOOK_DROPS_DIAGNOSIS.md before pointing real traffic at it):
# web: gunicorn maya_server:app --bind 0.0.0.0:$PORT --timeout 120

web: gunicorn ceo_live_server:app --bind 0.0.0.0:$PORT --workers 2 --threads 8 --timeout 120
