use anoti_core::{EventKind, NotificationRequest, Urgency};

#[test]
fn notification_request_round_trips_json() {
    let request = NotificationRequest {
        app_name: "Claude Code".to_owned(),
        title: "Claude Code: Hoàn thành".to_owned(),
        message: "Claude đã hoàn thành trả lời.".to_owned(),
        questions_json: String::new(),
        urgency: Urgency::Normal,
        event_kind: EventKind::Complete,
        sound: "/usr/share/sounds/freedesktop/stereo/complete.oga".to_owned(),
        session_id: "test-session".to_owned(),
        timeout: 0,
        icon: "claude".to_owned(),
    };
    let json = serde_json::to_string(&request).expect("serialization should succeed");
    let deserialized: NotificationRequest =
        serde_json::from_str(&json).expect("deserialization should succeed");
    assert_eq!(deserialized, request);
}

#[test]
fn notification_request_accepts_event_type_alias() {
    let json = r#"{"app_name":"Codex","title":"Test","message":"Msg","event_type":"question","urgency":"critical"}"#;
    let request: NotificationRequest = serde_json::from_str(json).unwrap();
    assert_eq!(request.event_kind, EventKind::Question);
    assert_eq!(request.urgency, Urgency::Critical);
    assert_eq!(request.resolved_icon_name(), "codex");
}

#[test]
fn notification_request_resolves_icons() {
    let explicit = NotificationRequest {
        icon: "antigravity".to_owned(),
        ..NotificationRequest::default()
    };
    assert_eq!(explicit.resolved_icon_name(), "antigravity");

    let inferred_claude = NotificationRequest {
        app_name: "Claude Code".to_owned(),
        ..NotificationRequest::default()
    };
    assert_eq!(inferred_claude.resolved_icon_name(), "claude");

    let inferred_fallback = NotificationRequest {
        app_name: "Custom Agent".to_owned(),
        ..NotificationRequest::default()
    };
    assert_eq!(inferred_fallback.resolved_icon_name(), "anoti");
}

#[test]
fn resolve_icon_path_selects_extension_and_fallbacks() {
    use std::fs;
    let temp_dir = tempfile::tempdir().unwrap();
    let icons_dir = temp_dir.path().join(".local/share/anoti/icons");
    fs::create_dir_all(&icons_dir).unwrap();

    // 1. When PNG exists, it is selected
    fs::write(icons_dir.join("claude.png"), b"png data").unwrap();
    let resolved = anoti_core::resolve_icon_path("claude", Some(temp_dir.path()));
    assert_eq!(resolved, Some(icons_dir.join("claude.png")));

    // 2. When only SVG exists, it is selected
    fs::write(icons_dir.join("codex.svg"), b"<svg></svg>").unwrap();
    let resolved_svg = anoti_core::resolve_icon_path("codex", Some(temp_dir.path()));
    assert_eq!(resolved_svg, Some(icons_dir.join("codex.svg")));

    // 3. When agent icon is missing, falls back to anoti.png
    fs::write(icons_dir.join("anoti.png"), b"anoti png").unwrap();
    let fallback = anoti_core::resolve_icon_path("unknown_agent", Some(temp_dir.path()));
    assert_eq!(fallback, Some(icons_dir.join("anoti.png")));

    // 4. Explicit file path takes precedence
    let custom_file = temp_dir.path().join("custom-icon.png");
    fs::write(&custom_file, b"custom").unwrap();
    let custom_resolved = anoti_core::resolve_icon_path(&custom_file.to_string_lossy(), None);
    assert_eq!(custom_resolved, Some(custom_file));
}

#[test]
fn resolve_icon_path_prioritizes_png_over_svg_when_both_exist() {
    use std::fs;
    let temp_dir = tempfile::tempdir().unwrap();
    let icons_dir = temp_dir.path().join(".local/share/anoti/icons");
    fs::create_dir_all(&icons_dir).unwrap();

    fs::write(icons_dir.join("claude.png"), b"claude-png").unwrap();
    fs::write(icons_dir.join("claude.svg"), b"<svg>claude</svg>").unwrap();

    fs::write(icons_dir.join("codex.png"), b"codex-png").unwrap();
    fs::write(icons_dir.join("codex.svg"), b"<svg>codex</svg>").unwrap();

    fs::write(icons_dir.join("anoti.png"), b"anoti-png").unwrap();
    fs::write(icons_dir.join("anoti.svg"), b"<svg>anoti</svg>").unwrap();

    assert_eq!(
        anoti_core::resolve_icon_path("claude", Some(temp_dir.path())),
        Some(icons_dir.join("claude.png"))
    );
    assert_eq!(
        anoti_core::resolve_icon_path("codex", Some(temp_dir.path())),
        Some(icons_dir.join("codex.png"))
    );
    assert_eq!(
        anoti_core::resolve_icon_path("anoti", Some(temp_dir.path())),
        Some(icons_dir.join("anoti.png"))
    );
    assert_eq!(
        anoti_core::resolve_icon_path("unknown", Some(temp_dir.path())),
        Some(icons_dir.join("anoti.png"))
    );
}

#[test]
fn corrupted_json_is_rejected() {
    assert!(serde_json::from_str::<NotificationRequest>("{broken").is_err());
}
