use anoti_core::{EventKind, QueueItem, SessionRecord};

#[test]
fn reads_legacy_session_v3_fixture() {
    let record: SessionRecord = serde_json::from_str(include_str!("fixtures/session-v3.json"))
        .expect("session fixture should deserialize");
    assert_eq!(record.schema_version, 3);
    assert_eq!(record.window_id, "12345");
    assert_eq!(record.window_pid, 700);
    assert_eq!(record.caller_pid_chain, [900, 800, 700]);
}

#[test]
fn reads_legacy_queue_fixture_and_event_type_alias() {
    let item: QueueItem = serde_json::from_str(include_str!("fixtures/queue-item.json"))
        .expect("queue fixture should deserialize");
    assert_eq!(item.event_kind, EventKind::Permission);
    assert_eq!(item.session_id, "session-1");
    assert_eq!(item.caller_pid_chain, [900, 800]);
}

#[test]
fn corrupted_json_is_rejected_at_contract_boundary() {
    assert!(serde_json::from_str::<SessionRecord>("{broken").is_err());
}

#[test]
fn rust_session_writer_round_trips_legacy_schema() {
    let record: SessionRecord = serde_json::from_str(include_str!("fixtures/session-v3.json"))
        .expect("session fixture should deserialize");
    let encoded = serde_json::to_string(&record).expect("Rust session should serialize");
    let decoded: SessionRecord =
        serde_json::from_str(&encoded).expect("serialized session should remain readable");
    assert_eq!(decoded, record);
}

#[test]
fn rust_queue_writer_keeps_legacy_event_type_key() {
    let item: QueueItem = serde_json::from_str(include_str!("fixtures/queue-item.json"))
        .expect("queue fixture should deserialize");
    let encoded = serde_json::to_value(item).expect("Rust queue item should serialize");
    assert_eq!(encoded["event_type"], "permission");
    assert!(encoded.get("event_kind").is_none());
    assert_eq!(encoded["caller_pid_chain"], serde_json::json!([900, 800]));
}
