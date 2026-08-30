//! CLI and runtime orchestration for the `anoti` binary.

use std::env;
use std::error::Error;
use std::io::{self, Read, Write};
use std::process::{Command, Stdio};
use std::time::Duration;

use anoti_core::{
    DedupeStore, EventKind, NotificationRequest, PlatformCapabilities, RuntimePaths, Urgency,
};
use anoti_hooks::{Agent, HookAction, HookContext};
use anoti_platform::PlatformBackend;
use clap::{Args, Parser, Subcommand, ValueEnum};
use serde_json::json;
use thiserror::Error;

mod lifecycle;

#[derive(Debug, Error)]
enum AppError {
    #[error("notification request is invalid: {0}")]
    InvalidRequest(String),
}

#[allow(clippy::struct_excessive_bools)]
#[derive(Debug, Parser)]
#[command(
    name = "anoti",
    version,
    about = "Cross-platform AI agent desktop notifier",
    disable_version_flag = true
)]
pub struct Cli {
    #[command(subcommand)]
    command: Option<CliCommand>,

    #[arg(long, short = 'u')]
    update: bool,
    #[arg(long)]
    uninstall: bool,
    #[arg(long)]
    install: bool,
    #[arg(long, short = 't')]
    test: bool,
    #[arg(long, short = 's')]
    status: bool,
    #[arg(long, short = 'c')]
    config: bool,
    #[arg(long, short = 'v')]
    version: bool,

    #[command(flatten)]
    notification: NotifyArgs,
}

#[derive(Debug, Subcommand)]
pub enum CliCommand {
    /// Inspect runtime capabilities and installed artifacts.
    #[command(visible_aliases = ["doc", "check"])]
    Doctor,
    /// Display runtime and integration status.
    #[command(visible_aliases = ["st", "info"])]
    Status,
    /// Send a test notification.
    #[command(visible_alias = "t")]
    Test,
    /// Display the configuration location.
    #[command(visible_alias = "cfg")]
    Config,
    /// Install native runtime integrations.
    #[command(visible_alias = "reinstall")]
    Install,
    /// Update native runtime integrations.
    #[command(visible_alias = "up")]
    Update,
    /// Remove native runtime integrations.
    #[command(visible_aliases = ["remove", "rm"])]
    Uninstall,
    /// Send a notification request.
    Notify(Box<NotifyArgs>),
    /// Handle a Claude Code, Codex, or Antigravity hook payload.
    Hook {
        #[arg(value_enum)]
        agent: AgentArg,
        /// Optional JSON payload. If omitted, the payload is read from stdin.
        payload: Option<String>,
    },
}

#[derive(Debug, Clone, Default, Args)]
pub struct NotifyArgs {
    #[arg(long)]
    pub app_name: Option<String>,
    #[arg(long)]
    pub title: Option<String>,
    #[arg(long, short = 'm')]
    pub message: Option<String>,
    #[arg(long)]
    pub questions_json: Option<String>,
    #[arg(long, value_enum, default_value = "normal")]
    pub urgency: UrgencyArg,
    #[arg(long, value_enum, default_value = "info")]
    pub event_type: EventArg,
    #[arg(long)]
    pub sound: Option<String>,
    #[arg(long)]
    pub session_id: Option<String>,
    #[arg(long, default_value_t = 0)]
    pub timeout: u64,
    #[arg(long, short = 'i')]
    pub icon: Option<String>,
    #[arg(long, hide = true)]
    pub request_json: Option<String>,
}

#[derive(Debug, Clone, Copy, Default, ValueEnum)]
pub enum UrgencyArg {
    Low,
    #[default]
    Normal,
    Critical,
}

impl From<UrgencyArg> for Urgency {
    fn from(value: UrgencyArg) -> Self {
        match value {
            UrgencyArg::Low => Self::Low,
            UrgencyArg::Normal => Self::Normal,
            UrgencyArg::Critical => Self::Critical,
        }
    }
}

#[derive(Debug, Clone, Copy, Default, ValueEnum)]
pub enum EventArg {
    Question,
    Permission,
    Complete,
    #[default]
    Info,
}

impl From<EventArg> for EventKind {
    fn from(value: EventArg) -> Self {
        match value {
            EventArg::Question => Self::Question,
            EventArg::Permission => Self::Permission,
            EventArg::Complete => Self::Complete,
            EventArg::Info => Self::Info,
        }
    }
}

#[derive(Debug, Clone, Copy, ValueEnum)]
pub enum AgentArg {
    Claude,
    Codex,
    Antigravity,
}

impl From<AgentArg> for Agent {
    fn from(value: AgentArg) -> Self {
        match value {
            AgentArg::Claude => Self::Claude,
            AgentArg::Codex => Self::Codex,
            AgentArg::Antigravity => Self::Antigravity,
        }
    }
}

pub fn run() -> Result<(), Box<dyn Error>> {
    run_cli(Cli::parse())
}

pub fn run_cli(cli: Cli) -> Result<(), Box<dyn Error>> {
    if let Some(command) = cli.command {
        return run_command(command);
    }
    if cli.version {
        println!("anoti {}", env!("CARGO_PKG_VERSION"));
    } else if cli.update {
        run_lifecycle("update")?;
    } else if cli.uninstall {
        run_lifecycle("uninstall")?;
    } else if cli.install {
        run_lifecycle("install")?;
    } else if cli.test {
        run_test()?;
    } else if cli.status {
        run_status()?;
    } else if cli.config {
        run_config()?;
    } else if has_notification_input(&cli.notification) {
        run_notify(cli.notification)?;
    } else {
        run_status()?;
    }
    Ok(())
}

fn run_command(command: CliCommand) -> Result<(), Box<dyn Error>> {
    match command {
        CliCommand::Doctor => run_doctor(),
        CliCommand::Status => run_status(),
        CliCommand::Test => run_test(),
        CliCommand::Config => run_config(),
        CliCommand::Install => run_lifecycle("install"),
        CliCommand::Update => run_lifecycle("update"),
        CliCommand::Uninstall => run_lifecycle("uninstall"),
        CliCommand::Notify(args) => run_notify(*args),
        CliCommand::Hook { agent, payload } => run_hook(agent.into(), payload),
    }
}

fn run_hook(agent: Agent, payload: Option<String>) -> Result<(), Box<dyn Error>> {
    let raw_payload = if let Some(payload) = payload {
        payload
    } else {
        let mut payload = String::new();
        io::stdin().read_to_string(&mut payload)?;
        payload
    };
    let caller_pid = parent_process_id();
    let context = HookContext {
        caller_pid,
        cwd: env::current_dir().unwrap_or_default(),
        is_windows: cfg!(windows),
        silent: env::var("AGENT2AGENTS_INITIALIZING").is_ok_and(|value| value == "1")
            || env::var("A2A_SILENT").is_ok_and(|value| value == "1"),
    };
    let result = match anoti_hooks::parse(agent, &raw_payload, &context) {
        Ok(result) => result,
        Err(_) => anoti_hooks::HookResult {
            response: if agent == Agent::Antigravity {
                "{}".to_owned()
            } else {
                String::new()
            },
            actions: Vec::new(),
        },
    };
    if !result.response.is_empty() {
        println!("{}", result.response);
        io::stdout().flush()?;
    }
    for action in &result.actions {
        let _ = spawn_action(action);
    }
    Ok(())
}

fn spawn_action(action: &HookAction) -> io::Result<()> {
    let executable = env::current_exe()?;
    let mut command = Command::new(executable);
    match action {
        HookAction::Notify(request) => {
            command
                .arg("notify")
                .arg("--request-json")
                .arg(serde_json::to_string(request).map_err(io::Error::other)?);
        }
    }
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    configure_detached(&mut command);
    command.spawn().map(|_| ())
}

#[cfg(unix)]
fn configure_detached(command: &mut Command) {
    use std::os::unix::process::CommandExt;
    command.process_group(0);
}

#[cfg(windows)]
fn configure_detached(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    const DETACHED_PROCESS: u32 = 0x0000_0008;
    command.creation_flags(CREATE_NO_WINDOW | DETACHED_PROCESS);
}

#[cfg(not(any(unix, windows)))]
fn configure_detached(_command: &mut Command) {}

pub fn run_notify(args: NotifyArgs) -> Result<(), Box<dyn Error>> {
    let request = if let Some(request_json) = args.request_json {
        serde_json::from_str::<NotificationRequest>(&request_json)?
    } else {
        request_from_args(args)?
    };

    let paths = RuntimePaths::discover()?;
    if DedupeStore::new(paths).check_and_record(
        &request.app_name,
        &request.title,
        &request.message,
        Duration::from_secs(2),
    )? {
        return Ok(());
    }

    dispatch_webhooks(&request)?;

    if env::var_os("AI_AGENT_NOTIFIER_NO_UI").is_none() {
        let backend = platform_backend();
        let _ = backend.play_sound(&request.sound);
        backend.native_notify(&request)?;
    }
    Ok(())
}

fn dispatch_webhooks(request: &NotificationRequest) -> Result<(), Box<dyn Error>> {
    let config = anoti_delivery::config_path()?;
    let endpoints = anoti_delivery::load_webhooks(&config)?;
    let _ = anoti_delivery::dispatch_webhooks_async(endpoints, request.clone());
    Ok(())
}

fn request_from_args(args: NotifyArgs) -> Result<NotificationRequest, AppError> {
    let message = args.message.unwrap_or_default();
    if message.trim().is_empty() {
        return Err(AppError::InvalidRequest("message is required".to_owned()));
    }
    Ok(NotificationRequest {
        app_name: args.app_name.unwrap_or_else(|| "AI agent".to_owned()),
        title: args.title.unwrap_or_else(|| "AI agent notifier".to_owned()),
        message,
        questions_json: args.questions_json.unwrap_or_default(),
        urgency: args.urgency.into(),
        event_kind: args.event_type.into(),
        sound: args.sound.unwrap_or_default(),
        session_id: args.session_id.unwrap_or_default(),
        timeout: args.timeout,
        icon: args.icon.unwrap_or_default(),
    })
}

#[cfg(target_os = "linux")]
fn platform_backend() -> Box<dyn PlatformBackend> {
    Box::new(anoti_platform_linux::LinuxBackend::default())
}

#[cfg(windows)]
fn platform_backend() -> Box<dyn PlatformBackend> {
    Box::new(anoti_platform_windows::WindowsBackend::default())
}

#[cfg(not(any(target_os = "linux", windows)))]
fn platform_backend() -> Box<dyn PlatformBackend> {
    compile_error!("anoti supports only Linux and Windows");
}

fn run_test() -> Result<(), Box<dyn Error>> {
    let request = NotificationRequest {
        app_name: "Anoti".to_owned(),
        title: "Kiểm tra thông báo".to_owned(),
        message: "Rust runtime đã nhận yêu cầu kiểm tra.".to_owned(),
        ..NotificationRequest::default()
    };
    run_notify(NotifyArgs {
        request_json: Some(serde_json::to_string(&request)?),
        ..NotifyArgs::default()
    })?;
    println!("Đã xử lý yêu cầu thông báo kiểm tra.");
    Ok(())
}

fn run_status() -> Result<(), Box<dyn Error>> {
    let paths = RuntimePaths::discover()?;
    let config = anoti_delivery::config_path()?;
    let webhook_count = anoti_delivery::load_webhooks(&config)?.len();
    println!(
        "{}",
        serde_json::to_string_pretty(&json!({
            "runtime": "rust",
            "version": env!("CARGO_PKG_VERSION"),
            "platform": env::consts::OS,
            "runtime_dir": paths.root,
            "webhook_config": config,
            "valid_webhooks": webhook_count,
        }))?
    );
    Ok(())
}

fn run_doctor() -> Result<(), Box<dyn Error>> {
    let paths = RuntimePaths::discover()?;
    let capabilities = platform_backend().capabilities();
    let mut report = doctor_report(&capabilities, &paths, None);
    report
        .as_object_mut()
        .expect("doctor report is an object")
        .insert("installation".to_owned(), lifecycle::installation_report()?);
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}

fn run_config() -> Result<(), Box<dyn Error>> {
    let path = anoti_delivery::config_path()?;
    let created = anoti_delivery::ensure_default_config(&path)?;
    println!("{}", path.display());
    if created {
        eprintln!("Đã tạo cấu hình webhook mẫu; các URL trống sẽ không được gửi.");
    }
    Ok(())
}

fn doctor_report(
    capabilities: &PlatformCapabilities,
    paths: &RuntimePaths,
    backend_error: Option<&str>,
) -> serde_json::Value {
    json!({
        "platform": env::consts::OS,
        "architecture": env::consts::ARCH,
        "runtime_dir": paths.root,
        "runtime_state_writable": true,
        "platform_backend": capabilities.backend,
        "capabilities": {
            "native_notification": capability_status(capabilities.native_notification, backend_error),
        },
        "backend_error": backend_error,
    })
}

fn capability_status(enabled: bool, runtime_error: Option<&str>) -> &'static str {
    if runtime_error.is_some() {
        "runtime_failure"
    } else if enabled {
        "available"
    } else {
        "unavailable"
    }
}

fn run_lifecycle(operation: &str) -> Result<(), Box<dyn Error>> {
    let report = lifecycle::execute(operation)?;
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}

fn has_notification_input(args: &NotifyArgs) -> bool {
    args.message.is_some() || args.request_json.is_some()
}

#[cfg(target_os = "linux")]
fn parent_process_id() -> u32 {
    std::fs::read_to_string("/proc/self/stat")
        .ok()
        .and_then(|stat| stat.rsplit_once(") ").map(|(_, fields)| fields.to_owned()))
        .and_then(|fields| fields.split_whitespace().nth(1)?.parse().ok())
        .unwrap_or(0)
}

#[cfg(not(target_os = "linux"))]
fn parent_process_id() -> u32 {
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cli_subcommands_and_args_parse() {
        let legacy = Cli::try_parse_from([
            "anoti",
            "--app-name=Codex",
            "--message=Done",
            "--event-type=complete",
        ])
        .unwrap();
        assert_eq!(legacy.notification.message.as_deref(), Some("Done"));

        let hook = Cli::try_parse_from(["anoti", "hook", "antigravity", "{}"]);
        assert!(hook.is_ok());
        let aliases = Cli::try_parse_from(["anoti", "check"]);
        assert!(matches!(aliases.unwrap().command, Some(CliCommand::Doctor)));
    }

    #[test]
    fn notification_args_parse_correctly() {
        let request = request_from_args(NotifyArgs {
            app_name: Some("Codex".to_owned()),
            message: Some("Done".to_owned()),
            session_id: Some("session".to_owned()),
            event_type: EventArg::Complete,
            icon: Some("codex".to_owned()),
            ..NotifyArgs::default()
        })
        .unwrap();
        assert_eq!(request.app_name, "Codex");
        assert_eq!(request.session_id, "session");
        assert_eq!(request.event_kind, EventKind::Complete);
        assert_eq!(request.icon, "codex");
        assert_eq!(request.resolved_icon_name(), "codex");
    }

    #[test]
    fn doctor_reports_native_notification_status() {
        let directory = tempfile::tempdir().unwrap();
        let paths = RuntimePaths::from_root(directory.path().join("runtime")).unwrap();
        let capabilities = PlatformCapabilities {
            native_notification: true,
            backend: "freedesktop-dbus".to_owned(),
        };
        let report = doctor_report(&capabilities, &paths, None);
        assert_eq!(report["capabilities"]["native_notification"], "available");
        assert_eq!(report["platform_backend"], "freedesktop-dbus");
    }
}
