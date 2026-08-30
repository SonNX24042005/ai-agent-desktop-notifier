//! Payload adapters for supported coding agents.

pub mod config;

use std::collections::HashSet;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

use anoti_core::{EventKind, NotificationRequest, Urgency};
use serde_json::Value;
use thiserror::Error;

const TRANSCRIPT_TAIL_BYTES: u64 = 65_536;
const MESSAGE_LIMIT: usize = 400;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Agent {
    Claude,
    Codex,
    Antigravity,
}

#[derive(Debug, Clone, PartialEq)]
pub enum HookAction {
    Notify(NotificationRequest),
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct HookResult {
    /// Text returned synchronously to the agent hook protocol.
    pub response: String,
    /// Work that the CLI must execute after writing `response`.
    pub actions: Vec<HookAction>,
}

#[derive(Debug, Clone)]
pub struct HookContext {
    pub caller_pid: u32,
    pub cwd: PathBuf,
    pub is_windows: bool,
    pub silent: bool,
}

impl Default for HookContext {
    fn default() -> Self {
        Self {
            caller_pid: 0,
            cwd: PathBuf::new(),
            is_windows: cfg!(windows),
            silent: false,
        }
    }
}

#[derive(Debug, Error)]
pub enum HookError {
    #[error("hook payload must be valid JSON: {0}")]
    InvalidJson(#[from] serde_json::Error),
}

pub fn parse(
    agent: Agent,
    raw_payload: &str,
    context: &HookContext,
) -> Result<HookResult, HookError> {
    if raw_payload.trim().is_empty() {
        return Ok(empty_result(agent, false));
    }
    let payload: Value = serde_json::from_str(raw_payload)?;
    if context.silent || raw_payload.contains("Initializing imported session history") {
        return Ok(empty_result(agent, payload.get("toolCall").is_some()));
    }
    Ok(match agent {
        Agent::Claude => parse_claude(&payload, raw_payload, context),
        Agent::Codex => parse_codex(&payload, context),
        Agent::Antigravity => parse_antigravity(&payload, context),
    })
}

fn empty_result(agent: Agent, tool_call: bool) -> HookResult {
    let response = if agent == Agent::Antigravity {
        if tool_call {
            r#"{"decision": "allow"}"#.to_owned()
        } else {
            "{}".to_owned()
        }
    } else {
        String::new()
    };
    HookResult {
        response,
        actions: Vec::new(),
    }
}

fn parse_claude(payload: &Value, raw_payload: &str, context: &HookContext) -> HookResult {
    let event = first_string(payload, &["hook_event_name", "event"]);
    let notification_type = first_string(payload, &["notification_type", "type", "matcher"]);
    let session_id = first_string(payload, &["session_id", "sessionID", "session"]);

    if event == "SessionStart" || notification_type == "SessionStart" {
        return empty_result(Agent::Claude, false);
    }
    if raw_payload.contains("idle_prompt") || raw_payload.contains("agent_needs_input") {
        return empty_result(Agent::Claude, false);
    }

    let tool_name = value_string(payload.get("tool_name")).unwrap_or_default();
    let tool_input = payload.get("tool_input").cloned().unwrap_or(Value::Null);
    let is_question = raw_payload.contains("AskUserQuestion")
        || raw_payload.to_ascii_lowercase().contains("ask_question");
    let is_permission =
        raw_payload.contains("permission_prompt") || raw_payload.contains("PermissionRequest");
    let is_completion = raw_payload.contains("agent_completed")
        || raw_payload.contains(r#""Stop""#)
        || event == "Stop";

    let request = if is_question {
        let title = if tool_name.is_empty() {
            "Claude Code: Câu hỏi".to_owned()
        } else {
            format!("Claude Code: Câu hỏi ({tool_name})")
        };
        notification(
            "Claude Code",
            &title,
            &question_text(&tool_input, "Claude Code đang đặt câu hỏi cho bạn."),
            questions_json(&tool_input),
            Urgency::Critical,
            EventKind::Question,
            sound(context, true),
            session_id,
            "claude",
        )
    } else if is_permission {
        let title = if tool_name.is_empty() {
            "Claude Code: Cần cấp quyền".to_owned()
        } else {
            format!("Claude Code: Cần cấp quyền ({tool_name})")
        };
        notification(
            "Claude Code",
            &title,
            &permission_text(&tool_input, payload, "Claude cần bạn cấp quyền thực thi."),
            questions_json(&tool_input),
            Urgency::Critical,
            EventKind::Permission,
            sound(context, true),
            session_id,
            "claude",
        )
    } else if is_completion {
        notification(
            "Claude Code",
            "Claude Code: Hoàn thành",
            "Claude đã hoàn thành trả lời.",
            String::new(),
            Urgency::Normal,
            EventKind::Complete,
            sound(context, false),
            session_id,
            "claude",
        )
    } else {
        return empty_result(Agent::Claude, false);
    };
    HookResult {
        response: String::new(),
        actions: vec![HookAction::Notify(request)],
    }
}

fn parse_codex(payload: &Value, context: &HookContext) -> HookResult {
    let event = first_string(payload, &["hook_event_name", "event", "type"]);
    let session_id = first_string(
        payload,
        &[
            "session_id",
            "thread-id",
            "thread_id",
            "turn-id",
            "turn_id",
            "sessionId",
            "conversation_id",
        ],
    );
    let is_notify = event == "notify";
    let is_permission = event == "permission_request" || event == "PermissionRequest";

    let request = if is_notify {
        let title = first_string(payload, &["title", "summary"]);
        let title = if title.is_empty() {
            "Codex".to_owned()
        } else {
            title
        };
        let message = first_string(payload, &["message", "body", "text"]);
        let urgency = match first_string(payload, &["urgency", "level"]).as_str() {
            "critical" | "high" => Urgency::Critical,
            "low" => Urgency::Low,
            _ => Urgency::Normal,
        };
        let event_kind = if urgency == Urgency::Critical {
            EventKind::Question
        } else {
            EventKind::Info
        };
        notification(
            "Codex",
            &title,
            &message,
            String::new(),
            urgency,
            event_kind,
            sound(context, urgency == Urgency::Critical),
            session_id,
            "codex",
        )
    } else if is_permission {
        let tool_name = first_string(payload, &["tool_name", "command", "tool"]);
        let tool_input = payload.get("tool_input").cloned().unwrap_or(Value::Null);
        let title = if tool_name.is_empty() {
            "Codex: Cần cấp quyền".to_owned()
        } else {
            format!("Codex: Cần cấp quyền ({tool_name})")
        };
        notification(
            "Codex",
            &title,
            &permission_text(
                &tool_input,
                payload,
                "Codex đang chờ bạn cấp quyền tiếp tục.",
            ),
            questions_json(&tool_input),
            Urgency::Critical,
            EventKind::Permission,
            sound(context, true),
            session_id,
            "codex",
        )
    } else if event == "agent-turn-complete" {
        notification(
            "Codex",
            "Codex đã hoàn thành",
            "Codex đã hoàn thành lượt làm việc.",
            String::new(),
            Urgency::Normal,
            EventKind::Complete,
            sound(context, false),
            session_id,
            "codex",
        )
    } else {
        return empty_result(Agent::Codex, false);
    };

    HookResult {
        response: String::new(),
        actions: vec![HookAction::Notify(request)],
    }
}

fn parse_antigravity(payload: &Value, context: &HookContext) -> HookResult {
    let has_tool_call = payload.get("toolCall").is_some();
    let event = first_string(payload, &["hook_event_name", "event"]);
    let notification_type = first_string(payload, &["notification_type", "type"]);
    if matches!(
        notification_type.as_str(),
        "idle_prompt" | "agent_needs_input"
    ) || matches!(event.as_str(), "idle_prompt" | "agent_needs_input")
    {
        return empty_result(Agent::Antigravity, has_tool_call);
    }

    let session_id = first_string(payload, &["conversationId", "session_id", "sessionId"]);

    if payload.get("invocationNum").is_some()
        || matches!(event.as_str(), "PreInvocation" | "PostInvocation")
    {
        return empty_result(Agent::Antigravity, false);
    }

    let tool_call = payload.get("toolCall").unwrap_or(&Value::Null);
    let tool_name = value_string(tool_call.get("name"))
        .or_else(|| value_string(payload.get("tool_name")))
        .unwrap_or_default();
    let tool_input = tool_call
        .get("args")
        .cloned()
        .or_else(|| payload.get("tool_input").cloned())
        .unwrap_or(Value::Null);
    let is_question = matches!(
        tool_name.as_str(),
        "ask_question" | "AskQuestion" | "AskUserQuestion" | "ask_user"
    ) || tool_name.to_ascii_lowercase().contains("ask");

    if has_tool_call && !is_question {
        return HookResult {
            response: r#"{"decision": "allow"}"#.to_owned(),
            actions: Vec::new(),
        };
    }
    if !is_question && !is_genuine_antigravity_completion(payload) {
        return empty_result(Agent::Antigravity, has_tool_call);
    }

    let (title, message, questions, urgency, event_kind, warning) = if is_question {
        (
            "Antigravity: Câu hỏi",
            question_text(&tool_input, "Antigravity đang đặt câu hỏi cho bạn."),
            questions_json(&tool_input),
            Urgency::Critical,
            EventKind::Question,
            true,
        )
    } else {
        (
            "Antigravity: Hoàn thành",
            "Antigravity đã hoàn thành trả lời.".to_owned(),
            String::new(),
            Urgency::Normal,
            EventKind::Complete,
            false,
        )
    };
    let response = if is_question {
        r#"{"decision": "allow"}"#.to_owned()
    } else {
        "{}".to_owned()
    };
    HookResult {
        response,
        actions: vec![HookAction::Notify(notification(
            "Antigravity",
            title,
            &message,
            questions,
            urgency,
            event_kind,
            sound(context, warning),
            session_id,
            "antigravity",
        ))],
    }
}

fn is_genuine_antigravity_completion(payload: &Value) -> bool {
    if payload.get("fullyIdle") == Some(&Value::Bool(false))
        || has_error_payload(payload)
        || value_string(payload.get("terminationReason"))
            .is_some_and(|reason| reason.eq_ignore_ascii_case("ERROR"))
    {
        return false;
    }
    let event = first_string(payload, &["hook_event_name", "event"]);
    let notification_type = first_string(payload, &["notification_type", "type"]);
    let has_stop_signal = value_string(payload.get("terminationReason"))
        .is_some_and(|value| !value.is_empty())
        || matches!(event.as_str(), "Stop" | "agent_completed")
        || notification_type == "agent_completed"
        || payload.get("fullyIdle") == Some(&Value::Bool(true));
    if !has_stop_signal {
        return false;
    }

    let Some(transcript_path) = value_string(payload.get("transcriptPath")) else {
        return true;
    };
    let preferred = transcript_path.replace("transcript_full.jsonl", "transcript.jsonl");
    for path in [preferred.as_str(), transcript_path.as_str()] {
        if Path::new(path).is_file()
            && let Ok(tail) = read_file_tail(Path::new(path), TRANSCRIPT_TAIL_BYTES)
        {
            return transcript_allows_completion(&tail);
        }
    }
    true
}

fn has_error_payload(payload: &Value) -> bool {
    for key in ["error", "last_error", "lastError"] {
        if payload.get(key).is_some_and(|error| match error {
            Value::Null => false,
            Value::String(s) => !s.trim().is_empty(),
            Value::Bool(b) => *b,
            Value::Array(a) => !a.is_empty(),
            Value::Object(o) => !o.is_empty(),
            Value::Number(n) => n.as_i64().is_some_and(|code| code != 0),
        }) {
            return true;
        }
    }
    false
}

fn read_file_tail(path: &Path, limit: u64) -> std::io::Result<String> {
    let mut file = File::open(path)?;
    let len = file.metadata()?.len();
    file.seek(SeekFrom::Start(len.saturating_sub(limit)))?;
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)?;
    Ok(String::from_utf8_lossy(&bytes).into_owned())
}

fn transcript_allows_completion(data: &str) -> bool {
    let mut running = HashSet::new();
    let mut completed = HashSet::new();
    let mut planner_steps = Vec::new();
    for line in data.lines().filter(|line| !line.trim().is_empty()) {
        let Ok(step) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if step.get("status").and_then(Value::as_str) == Some("RUNNING")
            && let Some(task_id) = task_id_after(step.get("content"), "task id:")
        {
            running.insert(task_id);
        }
        if step.get("source").and_then(Value::as_str) == Some("SYSTEM")
            && step.get("type").and_then(Value::as_str) == Some("SYSTEM_MESSAGE")
            && let Some(task_id) = completed_task_id(step.get("content"))
        {
            completed.insert(task_id);
        }
        if step.get("source").and_then(Value::as_str) == Some("MODEL")
            && step.get("type").and_then(Value::as_str) == Some("PLANNER_RESPONSE")
        {
            planner_steps.push(step);
        }
    }
    if running.difference(&completed).next().is_some() {
        return false;
    }
    planner_steps.last().is_none_or(|step| {
        !step
            .get("tool_calls")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|call| call.get("name").and_then(Value::as_str))
            .any(|name| name.to_ascii_lowercase().contains("ask"))
    })
}

fn task_id_after(content: Option<&Value>, marker: &str) -> Option<String> {
    let content = content?.as_str()?;
    let (_, rest) = content.split_once(marker)?;
    rest.split_whitespace().next().map(ToOwned::to_owned)
}

fn completed_task_id(content: Option<&Value>) -> Option<String> {
    let content = content?.as_str()?;
    if !content.contains("finished with result") {
        return None;
    }
    let (_, rest) = content.split_once("Task id \"")?;
    let (task_id, _) = rest.split_once('"')?;
    Some(task_id.to_owned())
}

#[allow(clippy::too_many_arguments)]
fn notification(
    app_name: &str,
    title: &str,
    message: &str,
    questions_json: String,
    urgency: Urgency,
    event_kind: EventKind,
    sound: String,
    session_id: String,
    icon: &str,
) -> NotificationRequest {
    NotificationRequest {
        app_name: app_name.to_owned(),
        title: title.to_owned(),
        message: clean_text(message, MESSAGE_LIMIT),
        questions_json,
        urgency,
        event_kind,
        sound,
        session_id,
        timeout: 0,
        icon: icon.to_owned(),
    }
}

fn sound(context: &HookContext, warning: bool) -> String {
    if context.is_windows {
        String::new()
    } else if warning {
        "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga".to_owned()
    } else {
        "/usr/share/sounds/freedesktop/stereo/complete.oga".to_owned()
    }
}

fn first_string(payload: &Value, keys: &[&str]) -> String {
    keys.iter()
        .find_map(|key| value_string(payload.get(*key)))
        .unwrap_or_default()
}

fn value_string(value: Option<&Value>) -> Option<String> {
    match value? {
        Value::String(value) => Some(value.clone()),
        Value::Number(value) => Some(value.to_string()),
        _ => None,
    }
}

fn questions_json(input: &Value) -> String {
    if input.is_null() {
        String::new()
    } else {
        serde_json::to_string(input).unwrap_or_default()
    }
}

fn question_text(input: &Value, fallback: &str) -> String {
    if let Some(questions) = input.get("questions").and_then(Value::as_array) {
        let text = questions
            .iter()
            .filter_map(|question| {
                value_string(question.get("question"))
                    .or_else(|| value_string(question.get("title")))
            })
            .filter(|text| !text.is_empty())
            .collect::<Vec<_>>()
            .join(" | ");
        if !text.is_empty() {
            return text;
        }
    }
    for key in ["question", "prompt"] {
        if let Some(text) = value_string(input.get(key))
            && !text.is_empty()
        {
            return text;
        }
    }
    fallback.to_owned()
}

fn permission_text(input: &Value, payload: &Value, fallback: &str) -> String {
    for key in ["description", "command"] {
        if let Some(text) = value_string(input.get(key))
            && !text.is_empty()
        {
            return text;
        }
    }
    if let Some(text) = value_string(payload.get("message"))
        && !text.is_empty()
    {
        return text;
    }
    if let Some(text) = input.as_str()
        && !text.is_empty()
    {
        return text.to_owned();
    }
    fallback.to_owned()
}

fn clean_text(value: &str, limit: usize) -> String {
    let text = value.split_whitespace().collect::<Vec<_>>().join(" ");
    if text.chars().count() <= limit {
        return text;
    }
    text.chars().take(limit - 3).collect::<String>() + "..."
}

#[cfg(test)]
mod tests {
    use std::fs;
    use tempfile::tempdir;

    use super::*;

    fn context() -> HookContext {
        HookContext {
            caller_pid: 42,
            cwd: PathBuf::from("/workspace/fallback"),
            is_windows: false,
            silent: false,
        }
    }

    #[test]
    fn claude_contracts_are_normalized() {
        let session_start = parse(
            Agent::Claude,
            r#"{"hook_event_name":"SessionStart","session_id":"s1","cwd":"C:\\work\\alpha"}"#,
            &context(),
        )
        .unwrap();
        assert!(session_start.actions.is_empty());

        let question = parse(
            Agent::Claude,
            r#"{"tool_name":"AskUserQuestion","tool_input":{"questions":[{"question":"Tiếp tục?"}]},"session_id":"s2"}"#,
            &context(),
        )
        .unwrap();
        let HookAction::Notify(request) = &question.actions[0];
        assert_eq!(request.event_kind, EventKind::Question);
        assert_eq!(request.message, "Tiếp tục?");
        assert_eq!(request.urgency, Urgency::Critical);
        assert_eq!(request.icon, "claude");
    }

    #[test]
    fn codex_contracts_are_normalized() {
        let permission = parse(
            Agent::Codex,
            r#"{"hook_event_name":"PermissionRequest","tool_name":"exec","tool_input":{"description":"Run tests"},"session_id":"s2"}"#,
            &context(),
        )
        .unwrap();
        let HookAction::Notify(request) = &permission.actions[0];
        assert_eq!(request.event_kind, EventKind::Permission);
        assert_eq!(request.message, "Run tests");
        assert_eq!(request.icon, "codex");

        let complete = parse(
            Agent::Codex,
            r#"{"type":"agent-turn-complete","thread-id":"thread"}"#,
            &context(),
        )
        .unwrap();
        assert!(matches!(
            complete.actions.first(),
            Some(HookAction::Notify(NotificationRequest {
                event_kind: EventKind::Complete,
                session_id,
                icon,
                ..
            })) if session_id == "thread" && icon == "codex"
        ));
    }

    #[test]
    fn antigravity_question_and_tool_contracts_are_normalized() {
        let question = parse(
            Agent::Antigravity,
            r#"{"conversationId":"a1","toolCall":{"name":"ask_question","args":{"questions":[{"question":"Chọn gì?"}]}}}"#,
            &context(),
        )
        .unwrap();
        assert_eq!(question.response, r#"{"decision": "allow"}"#);
        let Some(HookAction::Notify(request)) = question.actions.first() else {
            panic!("expected notification");
        };
        assert_eq!(request.event_kind, EventKind::Question);
        assert_eq!(request.icon, "antigravity");

        let other = parse(
            Agent::Antigravity,
            r#"{"conversationId":"a1","toolCall":{"name":"run_command"}}"#,
            &context(),
        )
        .unwrap();
        assert_eq!(other.response, r#"{"decision": "allow"}"#);
        assert!(other.actions.is_empty());
    }

    #[test]
    fn antigravity_completion_rejects_running_task_and_pending_question() {
        let directory = tempdir().unwrap();
        let transcript = directory.path().join("transcript.jsonl");
        fs::write(
            &transcript,
            concat!(
                r#"{"status":"RUNNING","content":"task id: job-1 now"}"#,
                "\n",
                r#"{"source":"MODEL","type":"PLANNER_RESPONSE","tool_calls":[]}"#,
                "\n"
            ),
        )
        .unwrap();
        let raw = serde_json::json!({
            "hook_event_name": "Stop",
            "fullyIdle": true,
            "transcriptPath": transcript,
        })
        .to_string();
        assert!(
            parse(Agent::Antigravity, &raw, &context())
                .unwrap()
                .actions
                .is_empty()
        );

        fs::write(
            &transcript,
            r#"{"source":"MODEL","type":"PLANNER_RESPONSE","tool_calls":[{"name":"ask_question"}]}"#,
        )
        .unwrap();
        assert!(
            parse(Agent::Antigravity, &raw, &context())
                .unwrap()
                .actions
                .is_empty()
        );
    }

    #[test]
    fn antigravity_completion_accepts_completed_task() {
        let directory = tempdir().unwrap();
        let transcript = directory.path().join("transcript.jsonl");
        fs::write(
            &transcript,
            concat!(
                r#"{"status":"RUNNING","content":"task id: job-1 now"}"#,
                "\n",
                r#"{"source":"SYSTEM","type":"SYSTEM_MESSAGE","content":"Task id \"job-1\" finished with result: ok"}"#,
                "\n",
                r#"{"source":"MODEL","type":"PLANNER_RESPONSE","tool_calls":[]}"#,
                "\n"
            ),
        )
        .unwrap();
        let raw = serde_json::json!({
            "hook_event_name": "Stop",
            "fullyIdle": true,
            "transcriptPath": transcript,
        })
        .to_string();
        let result = parse(Agent::Antigravity, &raw, &context()).unwrap();
        assert!(matches!(
            result.actions.first(),
            Some(HookAction::Notify(NotificationRequest {
                event_kind: EventKind::Complete,
                ..
            }))
        ));
    }

    #[test]
    fn antigravity_completion_accepts_real_world_stop_payload_with_empty_error() {
        let raw = serde_json::json!({
            "executionNum": 1,
            "terminationReason": "model_stop",
            "error": "",
            "fullyIdle": true,
            "conversationId": "agy-real-conv",
            "workspacePaths": ["/workspace/test"],
            "modelName": "auto"
        })
        .to_string();
        let result = parse(Agent::Antigravity, &raw, &context()).unwrap();
        assert_eq!(result.response, "{}");
        let Some(HookAction::Notify(request)) = result.actions.first() else {
            panic!("expected notification action for real-world completion");
        };
        assert_eq!(request.event_kind, EventKind::Complete);
        assert_eq!(request.app_name, "Antigravity");
        assert_eq!(request.title, "Antigravity: Hoàn thành");
        assert_eq!(request.message, "Antigravity đã hoàn thành trả lời.");
        assert_eq!(request.urgency, Urgency::Normal);
        assert_eq!(request.session_id, "agy-real-conv");
        assert_eq!(request.icon, "antigravity");
    }

    #[test]
    fn antigravity_completion_rejects_actual_error_and_error_reason() {
        let with_error_msg = serde_json::json!({
            "executionNum": 1,
            "terminationReason": "model_stop",
            "error": "syntax error in user script",
            "fullyIdle": true,
            "conversationId": "agy-err-1"
        })
        .to_string();
        assert!(
            parse(Agent::Antigravity, &with_error_msg, &context())
                .unwrap()
                .actions
                .is_empty()
        );

        let with_error_reason = serde_json::json!({
            "executionNum": 1,
            "terminationReason": "error",
            "error": "",
            "fullyIdle": true,
            "conversationId": "agy-err-2"
        })
        .to_string();
        assert!(
            parse(Agent::Antigravity, &with_error_reason, &context())
                .unwrap()
                .actions
                .is_empty()
        );
    }

    #[test]
    fn antigravity_completion_rejects_busy_or_invocation_lifecycle() {
        let not_idle = serde_json::json!({
            "executionNum": 1,
            "terminationReason": "model_stop",
            "error": "",
            "fullyIdle": false,
            "conversationId": "agy-busy"
        })
        .to_string();
        assert!(
            parse(Agent::Antigravity, &not_idle, &context())
                .unwrap()
                .actions
                .is_empty()
        );

        let pre_invocation = serde_json::json!({
            "invocationNum": 1,
            "initialNumSteps": 5,
            "conversationId": "agy-pre"
        })
        .to_string();
        let result_pre = parse(Agent::Antigravity, &pre_invocation, &context()).unwrap();
        assert_eq!(result_pre.response, "{}");
        assert!(result_pre.actions.is_empty());

        let post_invocation = serde_json::json!({
            "hook_event_name": "PostInvocation",
            "invocationNum": 1,
            "conversationId": "agy-post"
        })
        .to_string();
        let result_post = parse(Agent::Antigravity, &post_invocation, &context()).unwrap();
        assert_eq!(result_post.response, "{}");
        assert!(result_post.actions.is_empty());
    }

    #[test]
    fn antigravity_completion_rejects_execution_num_without_stop_signal() {
        let only_execution_num = serde_json::json!({
            "executionNum": 1,
            "conversationId": "agy-no-stop"
        })
        .to_string();
        assert!(
            parse(Agent::Antigravity, &only_execution_num, &context())
                .unwrap()
                .actions
                .is_empty()
        );
    }
}
