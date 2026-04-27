from core.shared_data_layer import SharedDataLayer
sdl = SharedDataLayer()
sid = sdl.create_session({"source": "streamlit", "note": "perception mvp"})
print("SID:", sid)

sdl.save_upload_bytes(sid, "events.csv", b"case_id,activity,timestamp\n1,A,2024-01-01")
sdl.save_clean_xes(sid, b"<log></log>")
sdl.save_dq_report(sid, {"completeness": 0.98, "ts_order_ok": True})

print(sdl.list_sessions())
print(list(sdl.list_uploads(sid)))
print(len(sdl.read_clean_xes(sid)))
print(sdl.read_dq_report(sid))
