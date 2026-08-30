use std::path::PathBuf;

use anoti_core::{EventKind, Urgency};
use anoti_hooks::{Agent, HookAction, HookContext, parse};
use serde_json::Value;

#[test]
fn rust_matches_locked_legacy_hook_contracts() {
    let fixtures: Vec<Value> =
        serde_json::from_str(include_str!("fixtures/legacy-hook-contracts.json")).unwrap();
    let context = HookContext {
        caller_pid: 42,
        cwd: PathBuf::from("/workspace/fallback"),
        is_windows: false,
        silent: false,
    };
    for fixture in fixtures {
        let agent = match fixture["agent"].as_str().unwrap() {
            "claude" => Agent::Claude,
            "codex" => Agent::Codex,
            "antigravity" => Agent::Antigravity,
            name => panic!("unknown fixture agent: {name}"),
        };
        let input = serde_json::to_string(&fixture["input"]).unwrap();
        let result = parse(agent, &input, &context).unwrap();
        assert_eq!(result.response, fixture["response"].as_str().unwrap());
        match fixture["action"].as_str().unwrap() {
            "none" => assert!(result.actions.is_empty()),
            "notify" => {
                let Some(HookAction::Notify(request)) = result.actions.first() else {
                    panic!("expected notify action for {agent:?}");
                };
                assert_eq!(request.app_name, fixture["app_name"].as_str().unwrap());
                assert_eq!(request.message, fixture["message"].as_str().unwrap());
                let expected_event = match fixture["event_type"].as_str().unwrap() {
                    "question" => EventKind::Question,
                    "permission" => EventKind::Permission,
                    "complete" => EventKind::Complete,
                    _ => EventKind::Info,
                };
                let expected_urgency = match fixture["urgency"].as_str().unwrap() {
                    "critical" => Urgency::Critical,
                    "low" => Urgency::Low,
                    _ => Urgency::Normal,
                };
                assert_eq!(request.event_kind, expected_event);
                assert_eq!(request.urgency, expected_urgency);
                if let Some(expected_icon) = fixture.get("icon").and_then(Value::as_str) {
                    assert_eq!(request.icon, expected_icon);
                }
            }
            action => panic!("unknown fixture action: {action}"),
        }
    }
}
