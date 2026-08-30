//! Ownership-aware JSON hook configuration updates.

use serde_json::{Map, Value};

/// Replaces only entries owned by anoti and preserves third-party hook entries.
pub fn merge_owned_hooks(
    document: &mut Value,
    additions: &Map<String, Value>,
    ownership_markers: &[&str],
) {
    if !document.is_object() {
        *document = Value::Object(Map::new());
    }
    let root = document.as_object_mut().expect("document was normalized");
    if !root.get("hooks").is_some_and(Value::is_object) {
        root.insert("hooks".to_owned(), Value::Object(Map::new()));
    }
    let hooks = root
        .get_mut("hooks")
        .and_then(Value::as_object_mut)
        .expect("hooks was normalized");

    // Clean up any stale owned entries across all hook events
    hooks.retain(|_, entries| {
        let Some(entries) = entries.as_array_mut() else {
            return true;
        };
        entries.retain(|entry| !contains_owned_marker(entry, ownership_markers));
        !entries.is_empty()
    });

    for (event, additions) in additions {
        let existing = hooks
            .get(event)
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut merged = existing;
        if let Some(entries) = additions.as_array() {
            merged.extend(entries.iter().cloned());
        }
        hooks.insert(event.clone(), Value::Array(merged));
    }
}

/// Removes only anoti-owned entries and prunes containers that become empty.
pub fn remove_owned_hooks(document: &mut Value, ownership_markers: &[&str]) {
    let Some(root) = document.as_object_mut() else {
        return;
    };
    let Some(hooks) = root.get_mut("hooks").and_then(Value::as_object_mut) else {
        return;
    };
    hooks.retain(|_, entries| {
        let Some(entries) = entries.as_array_mut() else {
            return true;
        };
        entries.retain(|entry| !contains_owned_marker(entry, ownership_markers));
        !entries.is_empty()
    });
    if hooks.is_empty() {
        root.remove("hooks");
    }
}

fn contains_owned_marker(value: &Value, markers: &[&str]) -> bool {
    match value {
        Value::String(value) => markers.iter().any(|marker| value.contains(marker)),
        Value::Array(values) => values
            .iter()
            .any(|value| contains_owned_marker(value, markers)),
        Value::Object(values) => values
            .values()
            .any(|value| contains_owned_marker(value, markers)),
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::*;

    #[test]
    fn merge_and_unmerge_preserve_third_party_entries_in_temp_config() {
        let directory = tempdir().unwrap();
        let path = directory.path().join("settings.json");
        fs::write(
            &path,
            r#"{"theme":"dark","hooks":{"Stop":[{"hooks":[{"command":"third-party"}]},{"hooks":[{"command":"old notify-input.sh"}]}]}}"#,
        )
        .unwrap();
        let mut document: Value =
            serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
        let additions = serde_json::json!({
            "Stop": [{"hooks": [{"type": "command", "command": "anoti hook claude"}]}],
            "SessionStart": [{"hooks": [{"type": "command", "command": "anoti hook claude"}]}]
        });
        merge_owned_hooks(
            &mut document,
            additions.as_object().unwrap(),
            &["notify-input.sh", "anoti hook claude"],
        );
        fs::write(&path, serde_json::to_vec_pretty(&document).unwrap()).unwrap();
        let merged = fs::read_to_string(&path).unwrap();
        assert!(merged.contains("third-party"));
        assert!(!merged.contains("old notify-input.sh"));
        assert_eq!(merged.matches("anoti hook claude").count(), 2);

        remove_owned_hooks(&mut document, &["anoti hook claude"]);
        assert_eq!(document["theme"], "dark");
        assert_eq!(document["hooks"]["Stop"].as_array().unwrap().len(), 1);
        assert!(document["hooks"].get("SessionStart").is_none());
    }
}
