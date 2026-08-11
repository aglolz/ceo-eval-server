# --workers 2 --threads 8: --threads switches gunicorn to the gthread worker,
# so up to 16 requests are in flight at once. The old config was ONE sync
# worker — while it sat in a minutes-long scoring request, every other webhook
# (including assistant-request, which Vapi needs answered in 7.5s to route the
# A/B arm) queued until Vapi reset the connection. Scoring itself now runs
# off-request in ceo_live_server.SCORING_EXECUTOR, so these threads only ever
# serve quick requests; --timeout 120 stays as a safety net.
#
# A test instance is NOT a separate entry point: run a second Railway service
# on this same Procfile with different env (see README "Running a test
# instance").

web: gunicorn ceo_live_server:app --bind 0.0.0.0:$PORT --workers 2 --threads 8 --timeout 120
