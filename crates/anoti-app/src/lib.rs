//! CLI and runtime orchestration for the `anoti` binary.

use std::env;
use std::error::Error;
use std::fs::{File, OpenOptions};
use std::io::{self, Read, Write};
use std::process::{Command, Stdio};
use std::time::Duration;

use anoti_core::{
    DedupeStore, EventKind, FocusOutcome, NotificationRequest, PlatformCapabilities, QueueItem,
    QueueStatus, QueueStore, RuntimePaths, SessionRecord, SessionStore, Urgency, WindowIdentity,
    epoch_seconds, normalize_pid_chain, queue_key,
};
use anoti_hooks::{Agent, HookAction, HookContext};
use anoti_platform::{IdentityQuery, PlatformBackend};
use clap::{Args, Parser, Subcommand, ValueEnum};
use fs2::FileExt;
use serde_json::json;
use thiserror::Error;

mod lifecycle;

#[derive(Debug, Error)]
enum AppError {
    #[error("notification request is invalid: {0}")]
    InvalidRequest(String),
}

#[derive(Debug, Parser)]
#[allow(clippy::struct_excessive_bools)]
#[command(
    name = "anoti",
    version,
    about = "Cross-platform AI agent desktop notifier",
    disable_version_flag = true
)]
struct Cli {
    #[command(subcommand)]
    command: Option<CliCommand>,

    #[arg(long, short = 'f')]
    focus: bool,
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
    #[arg(long, hide = true)]
    capture_session: bool,
    #[arg(long, hide = true)]
    dismiss: bool,

    #[command(flatten)]
    notification: NotifyArgs,
}

#[derive(Debug, Subcommand)]
enum CliCommand {
    /// Focus the oldest pending agent notification.
    #[command(visible_alias = "f")]
    Focus,
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
    /// Queue a notification request.
    Notify(Box<NotifyArgs>),
    /// Capture the source window identity for an agent session.
    #[command(hide = true)]
    CaptureSession(Box<CaptureArgs>),
    /// Dismiss a session notification from the persistent queue.
    #[command(hide = true)]
    Dismiss {
        #[arg(long)]
        session_id: String,
    },
    /// Handle a Claude Code, Codex, or Antigravity hook payload.
    Hook {
        #[arg(value_enum)]
        agent: AgentArg,
        /// Optional JSON payload. If omitted, the payload is read from stdin.
        payload: Option<String>,
    },
}

#[derive(Debug, Clone, Default, Args)]
struct NotifyArgs {
    #[arg(long)]
    app_name: Option<String>,
    #[arg(long)]
    title: Option<String>,
    #[arg(long, short = 'm')]
    message: Option<String>,
    #[arg(long)]
    questions_json: Option<String>,
    #[arg(long, value_enum, default_value = "normal")]
    urgency: UrgencyArg,
    #[arg(long, value_enum, default_value = "info")]
    event_type: EventArg,
    #[arg(long)]
    sound: Option<String>,
    #[arg(long)]
    window_id: Option<String>,
    #[arg(long, default_value_t = 0)]
    caller_pid: u32,
    #[arg(long)]
    caller_pid_chain: Option<String>,
    #[arg(long)]
    project_hint: Option<String>,
    #[arg(long)]
    app_hint: Option<String>,
    #[arg(long)]
    session_id: Option<String>,
    #[arg(long, default_value_t = 0)]
    timeout: u64,
    #[arg(long, default_value_t = 1.5)]
    auto_dismiss_delay: f64,
    #[arg(long, hide = true)]
    request_json: Option<String>,
    // Accepted for compatibility; platform discovery consumes them in later dispatch stages.
    #[arg(long, hide = true)]
    caller_tty: Option<String>,
    #[arg(long, hide = true)]
    terminal_screen: Option<String>,
}

#[derive(Debug, Clone, Args)]
struct CaptureArgs {
    #[arg(long)]
    app_name: String,
    #[arg(long)]
    session_id: String,
    #[arg(long)]
    window_id: Option<String>,
    #[arg(long, default_value_t = 0)]
    caller_pid: u32,
    #[arg(long)]
    caller_pid_chain: Option<String>,
    #[arg(long)]
    project_hint: Option<String>,
    #[arg(long)]
    app_hint: Option<String>,
    #[arg(long)]
    caller_tty: Option<String>,
    #[arg(long)]
    terminal_screen: Option<String>,
}

#[derive(Debug, Clone, Copy, Default, ValueEnum)]
enum UrgencyArg {
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
enum EventArg {
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
enum AgentArg {
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

fn run_cli(cli: Cli) -> Result<(), Box<dyn Error>> {
    if let Some(command) = cli.command {
        return run_command(command);
    }
    if cli.version {
        println!("anoti {}", env!("CARGO_PKG_VERSION"));
    } else if cli.capture_session {
        run_capture(&CaptureArgs {
            app_name: cli.notification.app_name.unwrap_or_default(),
            session_id: cli.notification.session_id.unwrap_or_default(),
            window_id: cli.notification.window_id,
            caller_pid: cli.notification.caller_pid,
            caller_pid_chain: cli.notification.caller_pid_chain,
            project_hint: cli.notification.project_hint,
            app_hint: cli.notification.app_hint,
            caller_tty: cli.notification.caller_tty,
            terminal_screen: cli.notification.terminal_screen,
        })?;
    } else if cli.dismiss {
        run_dismiss(cli.notification.session_id.as_deref().unwrap_or_default())?;
    } else if cli.focus {
        run_focus()?;
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
        CliCommand::Focus => run_focus(),
        CliCommand::Doctor => run_doctor(),
        CliCommand::Status => run_status(),
        CliCommand::Test => run_test(),
        CliCommand::Config => run_config(),
        CliCommand::Install => run_lifecycle("install"),
        CliCommand::Update => run_lifecycle("update"),
        CliCommand::Uninstall => run_lifecycle("uninstall"),
        CliCommand::Notify(args) => run_notify(*args),
        CliCommand::CaptureSession(args) => run_capture(args.as_ref()),
        CliCommand::Dismiss { session_id } => run_dismiss(&session_id),
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
        caller_pid_chain: process_ancestor_chain(caller_pid),
        cwd: env::current_dir().unwrap_or_default(),
        is_windows: cfg!(windows),
        silent: env::var("AGENT2AGENTS_INITIALIZING").is_ok_and(|value| value == "1")
            || env::var("A2A_SILENT").is_ok_and(|value| value == "1"),
        caller_tty: caller_terminal_tty(caller_pid),
        terminal_screen: env::var("GNOME_TERMINAL_SCREEN").unwrap_or_default(),
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
        if let HookAction::CaptureSession {
            app_name,
            session_id,
            project_hint,
            app_hint,
            caller_pid,
            caller_pid_chain,
            caller_tty,
            terminal_screen,
        } = action
        {
            run_capture(&CaptureArgs {
                app_name: app_name.clone(),
                session_id: session_id.clone(),
                window_id: None,
                caller_pid: *caller_pid,
                caller_pid_chain: Some(
                    caller_pid_chain
                        .iter()
                        .map(u32::to_string)
                        .collect::<Vec<_>>()
                        .join(","),
                ),
                project_hint: Some(project_hint.clone()),
                app_hint: Some(app_hint.clone()),
                caller_tty: Some(caller_tty.clone()),
                terminal_screen: Some(terminal_screen.clone()),
            })?;
        } else {
            let _ = spawn_action(action);
        }
    }
    Ok(())
}

fn spawn_action(action: &HookAction) -> io::Result<()> {
    let executable = env::current_exe()?;
    let mut command = Command::new(executable);
    match action {
        HookAction::CaptureSession {
            app_name,
            session_id,
            project_hint,
            app_hint,
            caller_pid,
            caller_pid_chain,
            caller_tty,
            terminal_screen,
        } => {
            command
                .arg("capture-session")
                .arg(format!("--app-name={app_name}"))
                .arg(format!("--session-id={session_id}"))
                .arg(format!("--project-hint={project_hint}"))
                .arg(format!("--app-hint={app_hint}"))
                .arg(format!("--caller-pid={caller_pid}"))
                .arg(format!("--caller-tty={caller_tty}"))
                .arg(format!("--terminal-screen={terminal_screen}"))
                .arg(format!(
                    "--caller-pid-chain={}",
                    caller_pid_chain
                        .iter()
                        .map(u32::to_string)
                        .collect::<Vec<_>>()
                        .join(",")
                ));
        }
        HookAction::Dismiss { session_id } => {
            command
                .arg("dismiss")
                .arg(format!("--session-id={session_id}"));
        }
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

fn run_notify(args: NotifyArgs) -> Result<(), Box<dyn Error>> {
    let mut request = if let Some(request_json) = args.request_json {
        serde_json::from_str::<NotificationRequest>(&request_json)?
    } else {
        request_from_args(args)?
    };
    hydrate_session_identity(&mut request)?;
    if queue_notification(&request)?.is_some() {
        dispatch_webhooks(&request)?;
        if env::var_os("AI_AGENT_NOTIFIER_NO_UI").is_none() {
            let paths = RuntimePaths::discover()?;
            let _lease = OverlayLease::acquire(&paths.overlay_lock)?;
            if let Some((key, item)) = QueueStore::new(paths).oldest_pending()? {
                deliver_notification(&key, &request_from_queue_item(&item))?;
            }
        }
    }
    Ok(())
}

struct OverlayLease {
    file: File,
}

impl OverlayLease {
    fn acquire(path: &std::path::Path) -> io::Result<Self> {
        let file = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(path)?;
        file.lock_exclusive()?;
        Ok(Self { file })
    }
}

impl Drop for OverlayLease {
    fn drop(&mut self) {
        let _ = FileExt::unlock(&self.file);
    }
}

fn dispatch_webhooks(request: &NotificationRequest) -> Result<(), Box<dyn Error>> {
    let config = anoti_delivery::config_path()?;
    let endpoints = anoti_delivery::load_webhooks(&config)?;
    let _ = anoti_delivery::dispatch_webhooks_async(endpoints, request.clone());
    Ok(())
}

fn hydrate_session_identity(request: &mut NotificationRequest) -> Result<(), Box<dyn Error>> {
    let Some(record) =
        SessionStore::new(RuntimePaths::discover()?).resolve_exact(&request.identity)?
    else {
        return Ok(());
    };
    apply_session_identity(request, record);
    Ok(())
}

fn apply_session_identity(request: &mut NotificationRequest, record: SessionRecord) {
    if !record.has_exact_window_identity() {
        return;
    }
    let session_id = request.identity.session_id.clone();
    request.identity = WindowIdentity {
        window_id: record.window_id,
        window_instance_id: record.window_instance_id,
        window_pid: record.window_pid,
        process_start_time: record.process_start_time,
        caller_pid: record.caller_pid,
        caller_pid_chain: record.caller_pid_chain,
        project_hint: record.project_hint,
        title_fingerprint: record.title_fingerprint,
        app_hint: record.app_hint,
        session_id,
        generation: record.generation,
    };
}

fn request_from_args(args: NotifyArgs) -> Result<NotificationRequest, AppError> {
    let message = args.message.unwrap_or_default();
    if message.trim().is_empty() {
        return Err(AppError::InvalidRequest("message is required".to_owned()));
    }
    let caller_pid_chain = args
        .caller_pid_chain
        .as_deref()
        .unwrap_or_default()
        .split(',')
        .filter_map(|part| part.trim().parse().ok())
        .collect();
    Ok(NotificationRequest {
        app_name: args.app_name.unwrap_or_else(|| "AI agent".to_owned()),
        title: args.title.unwrap_or_else(|| "AI agent notifier".to_owned()),
        message,
        questions_json: args.questions_json.unwrap_or_default(),
        urgency: args.urgency.into(),
        event_kind: args.event_type.into(),
        sound: args.sound.unwrap_or_default(),
        identity: WindowIdentity {
            window_id: args.window_id.unwrap_or_default(),
            caller_pid: args.caller_pid,
            caller_pid_chain,
            project_hint: args.project_hint.unwrap_or_default(),
            app_hint: args.app_hint.unwrap_or_default(),
            session_id: args.session_id.unwrap_or_default(),
            ..WindowIdentity::default()
        },
        timeout: args.timeout,
        auto_dismiss_delay: args.auto_dismiss_delay,
    })
}

fn queue_notification(request: &NotificationRequest) -> Result<Option<String>, Box<dyn Error>> {
    let paths = RuntimePaths::discover()?;
    if DedupeStore::new(paths.clone()).check_and_record(
        &request.app_name,
        &request.title,
        &request.message,
        Duration::from_secs(2),
    )? {
        return Ok(None);
    }
    let key = queue_key(&request.identity);
    QueueStore::new(paths).save(
        &key,
        QueueItem {
            app_name: request.app_name.clone(),
            title: request.title.clone(),
            message: request.message.clone(),
            questions_json: request.questions_json.clone(),
            urgency: request.urgency,
            event_kind: request.event_kind,
            sound: request.sound.clone(),
            target_window_id: request.identity.window_id.clone(),
            window_instance_id: request.identity.window_instance_id.clone(),
            window_pid: request.identity.window_pid,
            process_start_time: request.identity.process_start_time,
            caller_pid: request.identity.caller_pid,
            caller_pid_chain: request.identity.caller_pid_chain.clone(),
            project_hint: request.identity.project_hint.clone(),
            app_hint: request.identity.app_hint.clone(),
            title_fingerprint: request.identity.title_fingerprint.clone(),
            session_id: request.identity.session_id.clone(),
            timeout: request.timeout,
            auto_dismiss_delay: request.auto_dismiss_delay,
            status: QueueStatus::Queued,
            generation: request.identity.generation,
            created_at: epoch_seconds(),
        },
    )?;
    Ok(Some(key))
}

fn deliver_notification(key: &str, request: &NotificationRequest) -> Result<(), Box<dyn Error>> {
    let mut request = request.clone();
    hydrate_session_identity(&mut request)?;
    let paths = RuntimePaths::discover()?;
    let store = QueueStore::new(paths.clone());
    let mut displaying = queue_item_from_request(&request);
    displaying.status = QueueStatus::Displaying;
    store.save(key, displaying)?;
    let backend = platform_backend();
    let _ = backend.play_sound(&request.sound);
    match backend.show_overlay(&request) {
        Ok(outcome) if outcome.dismissed || outcome.focused => {
            store.remove(key)?;
        }
        Ok(_) => store.requeue_displaying(None)?,
        Err(_) => {
            let _ = backend.native_notify(&request);
            store.requeue_displaying(None)?;
        }
    }
    Ok(())
}

fn queue_item_from_request(request: &NotificationRequest) -> QueueItem {
    QueueItem {
        app_name: request.app_name.clone(),
        title: request.title.clone(),
        message: request.message.clone(),
        questions_json: request.questions_json.clone(),
        urgency: request.urgency,
        event_kind: request.event_kind,
        sound: request.sound.clone(),
        target_window_id: request.identity.window_id.clone(),
        window_instance_id: request.identity.window_instance_id.clone(),
        window_pid: request.identity.window_pid,
        process_start_time: request.identity.process_start_time,
        caller_pid: request.identity.caller_pid,
        caller_pid_chain: request.identity.caller_pid_chain.clone(),
        project_hint: request.identity.project_hint.clone(),
        app_hint: request.identity.app_hint.clone(),
        title_fingerprint: request.identity.title_fingerprint.clone(),
        session_id: request.identity.session_id.clone(),
        timeout: request.timeout,
        auto_dismiss_delay: request.auto_dismiss_delay,
        status: QueueStatus::Queued,
        generation: request.identity.generation,
        created_at: epoch_seconds(),
    }
}

fn request_from_queue_item(item: &QueueItem) -> NotificationRequest {
    NotificationRequest {
        app_name: item.app_name.clone(),
        title: item.title.clone(),
        message: item.message.clone(),
        questions_json: item.questions_json.clone(),
        urgency: item.urgency,
        event_kind: item.event_kind,
        sound: item.sound.clone(),
        identity: WindowIdentity {
            window_id: item.target_window_id.clone(),
            window_instance_id: item.window_instance_id.clone(),
            window_pid: item.window_pid,
            process_start_time: item.process_start_time,
            caller_pid: item.caller_pid,
            caller_pid_chain: item.caller_pid_chain.clone(),
            project_hint: item.project_hint.clone(),
            app_hint: item.app_hint.clone(),
            title_fingerprint: item.title_fingerprint.clone(),
            session_id: item.session_id.clone(),
            generation: item.generation,
        },
        timeout: item.timeout,
        auto_dismiss_delay: item.auto_dismiss_delay,
    }
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

fn run_capture(args: &CaptureArgs) -> Result<(), Box<dyn Error>> {
    if args.session_id.trim().is_empty() {
        return Ok(());
    }
    let paths = RuntimePaths::discover()?;
    let sessions = SessionStore::new(paths.clone());
    let backend = platform_backend();
    let backend_name = backend.capabilities().backend;
    let record = capture_session_record(args, &backend_name, |query| {
        backend.capture_identity(query).ok().flatten()
    });
    sessions.save_capture(&args.session_id, record)?;
    Ok(())
}

fn capture_session_record<F>(args: &CaptureArgs, backend_name: &str, capture: F) -> SessionRecord
where
    F: FnOnce(&IdentityQuery) -> Option<WindowIdentity>,
{
    let window_id = args.window_id.clone().unwrap_or_default();
    let caller_pid_chain = normalize_pid_chain(
        args.caller_pid_chain
            .as_deref()
            .unwrap_or_default()
            .split(',')
            .filter_map(|part| part.trim().parse().ok()),
        args.caller_pid,
    );
    let project_hint = args.project_hint.clone().unwrap_or_default();
    let app_hint = args
        .app_hint
        .clone()
        .unwrap_or_else(|| args.app_name.to_ascii_lowercase());
    let query = IdentityQuery {
        window_id: window_id.clone(),
        caller_pid: args.caller_pid,
        caller_pid_chain: caller_pid_chain.clone(),
        project_hint: project_hint.clone(),
        session_id: args.session_id.clone(),
        app_hint: app_hint.clone(),
        caller_tty: args.caller_tty.clone().unwrap_or_default(),
        terminal_screen: args.terminal_screen.clone().unwrap_or_default(),
        ..IdentityQuery::default()
    };
    if let Some(identity) = capture(&query) {
        let record = SessionRecord {
            window_id_dec: identity.window_id.clone(),
            window_id: identity.window_id,
            window_instance_id: identity.window_instance_id,
            project_hint: identity.project_hint,
            pid: identity.caller_pid,
            window_pid: identity.window_pid,
            process_start_time: identity.process_start_time,
            caller_pid: identity.caller_pid,
            caller_pid_chain: identity.caller_pid_chain,
            app_hint: identity.app_hint,
            title_fingerprint: identity.title_fingerprint,
            precision: "window".to_owned(),
            backend: backend_name.to_owned(),
            caller_tty: args.caller_tty.clone().unwrap_or_default(),
            terminal_screen: args.terminal_screen.clone().unwrap_or_default(),
            generation: identity.generation,
            updated_at: epoch_seconds(),
            ..SessionRecord::default()
        };
        if record.has_exact_window_identity() {
            return record;
        }
    }

    SessionRecord {
        window_id: window_id.clone(),
        window_id_dec: window_id,
        project_hint,
        pid: args.caller_pid,
        caller_pid: args.caller_pid,
        caller_pid_chain,
        app_hint,
        precision: "app".to_owned(),
        backend: "pending".to_owned(),
        caller_tty: args.caller_tty.clone().unwrap_or_default(),
        terminal_screen: args.terminal_screen.clone().unwrap_or_default(),
        updated_at: epoch_seconds(),
        ..SessionRecord::default()
    }
}

fn run_dismiss(session_id: &str) -> Result<(), Box<dyn Error>> {
    if session_id.trim().is_empty() {
        return Ok(());
    }
    let identity = WindowIdentity {
        session_id: session_id.to_owned(),
        ..WindowIdentity::default()
    };
    QueueStore::new(RuntimePaths::discover()?).remove(&queue_key(&identity))?;
    Ok(())
}

fn run_focus() -> Result<(), Box<dyn Error>> {
    let paths = RuntimePaths::discover()?;
    let store = QueueStore::new(paths);
    let pending = store.oldest_actionable()?;
    if let Some((key, item)) = pending {
        let mut request = request_from_queue_item(&item);
        hydrate_session_identity(&mut request)?;
        let query = identity_query(&request.identity);
        let backend = platform_backend();
        let outcome = match backend.resolve_target(&query)? {
            Some(target) => backend.focus(&target, &query)?,
            None => FocusOutcome::NotFound,
        };
        if matches!(outcome, FocusOutcome::Focused { .. }) {
            store.remove(&key)?;
        }
        println!(
            "{}",
            json!({"status":focus_status(&outcome),"key":key,"app_name":item.app_name,"session_id":item.session_id,"outcome":format!("{outcome:?}")})
        );
    } else {
        println!("{}", json!({"status": "empty"}));
    }
    Ok(())
}

fn identity_query(identity: &WindowIdentity) -> IdentityQuery {
    IdentityQuery {
        window_id: identity.window_id.clone(),
        window_instance_id: identity.window_instance_id.clone(),
        window_pid: identity.window_pid,
        process_start_time: identity.process_start_time,
        caller_pid: identity.caller_pid,
        caller_pid_chain: identity.caller_pid_chain.clone(),
        project_hint: identity.project_hint.clone(),
        session_id: identity.session_id.clone(),
        app_hint: identity.app_hint.clone(),
        title_fingerprint: identity.title_fingerprint.clone(),
        generation: identity.generation,
        ..IdentityQuery::default()
    }
}

const fn focus_status(outcome: &FocusOutcome) -> &'static str {
    match outcome {
        FocusOutcome::Focused { .. } => "focused",
        FocusOutcome::NotFound => "not_found",
        FocusOutcome::Ambiguous => "ambiguous",
        FocusOutcome::Failed { .. } => "failed",
        FocusOutcome::Unsupported { .. } => "unsupported",
    }
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
    let queue_count = QueueStore::new(paths.clone()).load()?.len();
    let config = anoti_delivery::config_path()?;
    let webhook_count = anoti_delivery::load_webhooks(&config)?.len();
    println!(
        "{}",
        serde_json::to_string_pretty(&json!({
            "runtime": "rust",
            "version": env!("CARGO_PKG_VERSION"),
            "platform": env::consts::OS,
            "runtime_dir": paths.root,
            "queued_notifications": queue_count,
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
            "window_discovery": capability_status(capabilities.window_discovery, backend_error),
            "active_window_probe": capability_status(capabilities.active_window_probe, backend_error),
            "focus": capability_status(capabilities.focus, backend_error),
            "precise_multi_monitor_placement": capability_status(
                capabilities.precise_multi_monitor_placement,
                backend_error,
            ),
            "native_notification": capability_status(capabilities.native_notification, backend_error),
            "global_hotkey": capability_status(capabilities.global_hotkey, backend_error),
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

#[cfg(target_os = "linux")]
fn process_ancestor_chain(start_pid: u32) -> Vec<u32> {
    let mut chain = Vec::new();
    let mut current = start_pid;
    while current > 1 && chain.len() < 32 && !chain.contains(&current) {
        chain.push(current);
        current = std::fs::read_to_string(format!("/proc/{current}/stat"))
            .ok()
            .and_then(|stat| stat.rsplit_once(") ").map(|(_, fields)| fields.to_owned()))
            .and_then(|fields| fields.split_whitespace().nth(1)?.parse().ok())
            .unwrap_or(0);
    }
    chain
}

#[cfg(target_os = "linux")]
fn caller_terminal_tty(start_pid: u32) -> String {
    for pid in process_ancestor_chain(start_pid) {
        for descriptor in [0, 1, 2] {
            let path = format!("/proc/{pid}/fd/{descriptor}");
            let Ok(target) = std::fs::read_link(path) else {
                continue;
            };
            let target = target.to_string_lossy();
            if target.starts_with("/dev/pts/") || target.starts_with("/dev/tty") {
                return target.into_owned();
            }
        }
    }
    String::new()
}

#[cfg(windows)]
fn parent_process_id() -> u32 {
    // The Windows backend resolves ancestry during capture; zero means unknown here.
    0
}

#[cfg(not(any(target_os = "linux", windows)))]
fn parent_process_id() -> u32 {
    0
}

#[cfg(not(target_os = "linux"))]
fn process_ancestor_chain(start_pid: u32) -> Vec<u32> {
    if start_pid > 1 {
        vec![start_pid]
    } else {
        Vec::new()
    }
}

#[cfg(not(target_os = "linux"))]
fn caller_terminal_tty(_start_pid: u32) -> String {
    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn legacy_and_subcommand_interfaces_parse() {
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
    fn notification_args_preserve_identity() {
        let request = request_from_args(NotifyArgs {
            app_name: Some("Codex".to_owned()),
            message: Some("Done".to_owned()),
            caller_pid: 41,
            caller_pid_chain: Some("41,12,1".to_owned()),
            session_id: Some("session".to_owned()),
            event_type: EventArg::Complete,
            ..NotifyArgs::default()
        })
        .unwrap();
        assert_eq!(request.identity.caller_pid_chain, [41, 12, 1]);
        assert_eq!(request.identity.session_id, "session");
        assert_eq!(request.event_kind, EventKind::Complete);
    }

    #[test]
    fn capture_session_uses_the_platform_identity_when_available() {
        let args = CaptureArgs {
            app_name: "Codex".to_owned(),
            session_id: "session".to_owned(),
            window_id: None,
            caller_pid: 41,
            caller_pid_chain: Some("41,12".to_owned()),
            project_hint: Some("project".to_owned()),
            app_hint: Some("codex".to_owned()),
            caller_tty: Some("/dev/pts/7".to_owned()),
            terminal_screen: Some("/org/gnome/Terminal/screen/example".to_owned()),
        };
        let record = capture_session_record(&args, "gnome-wayland-dbus", |query| {
            assert_eq!(query.caller_pid_chain, [41, 12]);
            Some(WindowIdentity {
                window_id: "wayland:3".to_owned(),
                window_instance_id: "wayland:wayland:3:99".to_owned(),
                window_pid: 99,
                caller_pid: query.caller_pid,
                caller_pid_chain: query.caller_pid_chain.clone(),
                project_hint: query.project_hint.clone(),
                title_fingerprint: "project - Visual Studio Code".to_owned(),
                app_hint: query.app_hint.clone(),
                session_id: query.session_id.clone(),
                generation: 0,
                process_start_time: 0,
            })
        });
        assert_eq!(record.window_id, "wayland:3");
        assert_eq!(record.window_pid, 99);
        assert_eq!(record.precision, "window");
        assert_eq!(record.backend, "gnome-wayland-dbus");
        assert_eq!(record.title_fingerprint, "project - Visual Studio Code");
        assert_eq!(record.caller_tty, "/dev/pts/7");
        assert_eq!(record.terminal_screen, "/org/gnome/Terminal/screen/example");
    }

    #[test]
    fn notification_uses_exact_session_snapshot_and_rejects_fake_marker() {
        let mut request = NotificationRequest {
            identity: WindowIdentity {
                session_id: "session".to_owned(),
                caller_pid: 71,
                ..WindowIdentity::default()
            },
            ..NotificationRequest::default()
        };
        apply_session_identity(
            &mut request,
            SessionRecord {
                window_id: "wayland:17".to_owned(),
                window_pid: 170,
                caller_pid: 71,
                caller_pid_chain: vec![71, 70],
                title_fingerprint: "project — Terminal".to_owned(),
                precision: "window".to_owned(),
                ..SessionRecord::default()
            },
        );
        assert_eq!(request.identity.window_id, "wayland:17");
        assert_eq!(request.identity.window_pid, 170);
        assert_eq!(request.identity.session_id, "session");

        apply_session_identity(
            &mut request,
            SessionRecord {
                window_id: "wayland:gnome-terminal".to_owned(),
                precision: "window".to_owned(),
                ..SessionRecord::default()
            },
        );
        assert_eq!(request.identity.window_id, "wayland:17");
    }

    #[test]
    fn failed_capture_is_never_promoted_to_window_precision() {
        let args = CaptureArgs {
            app_name: "Codex".to_owned(),
            session_id: "session".to_owned(),
            window_id: Some("wayland:gnome-terminal".to_owned()),
            caller_pid: 41,
            caller_pid_chain: Some("41,12".to_owned()),
            project_hint: Some("project".to_owned()),
            app_hint: Some("codex".to_owned()),
            caller_tty: None,
            terminal_screen: None,
        };
        let record = capture_session_record(&args, "gnome-wayland-dbus", |_| None);
        assert_eq!(record.precision, "app");
        assert_eq!(record.backend, "pending");
    }

    #[test]
    fn doctor_distinguishes_unavailable_from_runtime_failure() {
        let directory = tempfile::tempdir().unwrap();
        let paths = RuntimePaths::from_root(directory.path().join("runtime")).unwrap();
        let capabilities = PlatformCapabilities {
            focus: true,
            backend: "fake".to_owned(),
            ..PlatformCapabilities::default()
        };
        let healthy = doctor_report(&capabilities, &paths, None);
        assert_eq!(healthy["capabilities"]["focus"], "available");
        assert_eq!(
            healthy["capabilities"]["native_notification"],
            "unavailable"
        );
        let failed = doctor_report(&capabilities, &paths, Some("probe failed"));
        assert_eq!(failed["capabilities"]["focus"], "runtime_failure");
    }
}
