use std::cell::RefCell;
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, mpsc};
use std::time::{Duration, Instant};

use anoti_core::NotificationRequest;
use anoti_delivery::OverlayState;
use anoti_platform::{OverlayOutcome, PlatformError};
use gtk::glib::{self, ControlFlow};
use gtk::prelude::*;

use crate::LinuxMonitor;

type Probe = Arc<dyn Fn() -> bool + Send + Sync + 'static>;

#[allow(clippy::needless_pass_by_value, clippy::too_many_lines)]
pub fn show_overlay(
    request: &NotificationRequest,
    monitors: &[LinuxMonitor],
    focus: Probe,
    active: Probe,
) -> Result<OverlayOutcome, PlatformError> {
    gtk::init().map_err(|error| PlatformError::Operation(format!("initialize GTK: {error}")))?;
    let state = Arc::new(Mutex::new(OverlayState::default()));
    let focused = Arc::new(AtomicBool::new(false));
    let started = Instant::now();
    let placements = if monitors.is_empty() {
        vec![None]
    } else {
        monitors.iter().map(Some).collect()
    };
    let windows = Rc::new(RefCell::new(Vec::<gtk::Window>::new()));

    for monitor in placements {
        let window = gtk::Window::new(gtk::WindowType::Popup);
        window.set_title("AI agent notifier");
        window.set_decorated(false);
        window.set_keep_above(true);
        window.set_skip_taskbar_hint(true);
        window.set_skip_pager_hint(true);
        window.set_accept_focus(false);
        window.set_focus_on_map(false);
        window.set_resizable(false);
        window.set_default_size(380, 160);

        let container = gtk::Box::new(gtk::Orientation::Vertical, 8);
        container.set_margin_start(16);
        container.set_margin_end(16);
        container.set_margin_top(14);
        container.set_margin_bottom(14);
        let title = gtk::Label::new(Some(&format!("{} · {}", request.app_name, request.title)));
        title.set_xalign(0.0);
        let message = gtk::Label::new(Some(&request.message));
        message.set_xalign(0.0);
        message.set_line_wrap(true);
        let actions = gtk::Box::new(gtk::Orientation::Horizontal, 8);
        let focus_button = gtk::Button::with_label("Chuyển đến cửa sổ");
        let dismiss_button = gtk::Button::with_label("Đóng");
        actions.pack_start(&focus_button, false, false, 0);
        actions.pack_end(&dismiss_button, false, false, 0);
        container.pack_start(&title, false, false, 0);
        container.pack_start(&message, true, true, 0);
        container.pack_start(&actions, false, false, 0);
        window.add(&container);
        window.add_events(gtk::gdk::EventMask::BUTTON_PRESS_MASK);

        let state_for_background = Arc::clone(&state);
        let windows_for_background = Rc::clone(&windows);
        let focus_button_for_background = focus_button.clone();
        let dismiss_button_for_background = dismiss_button.clone();
        window.connect_button_press_event(move |window, event| {
            if event.button() != 1 {
                return glib::Propagation::Proceed;
            }
            let (x, y) = event.position();
            let originated_from_button =
                point_in_widget(&focus_button_for_background, window, x, y)
                    || point_in_widget(&dismiss_button_for_background, window, x, y);
            if let Ok(mut state) = state_for_background.lock() {
                state.background_click(originated_from_button);
            }
            if originated_from_button {
                return glib::Propagation::Proceed;
            }
            close_all(&windows_for_background);
            gtk::main_quit();
            glib::Propagation::Stop
        });

        let state_for_focus = Arc::clone(&state);
        let focus_probe = Arc::clone(&focus);
        let focus_label = focus_button.clone();
        let windows_for_focus = Rc::clone(&windows);
        let focused_flag = Arc::clone(&focused);
        focus_button.connect_clicked(move |_| {
            if !state_for_focus
                .lock()
                .is_ok_and(|mut state| state.request_focus())
            {
                return;
            }
            focus_label.set_label("Đang chuyển...");
            focus_label.set_sensitive(false);
            hide_all(&windows_for_focus);
            let (sender, receiver) = mpsc::sync_channel(1);
            let probe = Arc::clone(&focus_probe);
            let _ = std::thread::Builder::new()
                .name("anoti-ui-focus".to_owned())
                .spawn(move || {
                    let _ = sender.send(probe());
                });
            let state = Arc::clone(&state_for_focus);
            let label = focus_label.clone();
            let windows = Rc::clone(&windows_for_focus);
            let focused = Arc::clone(&focused_flag);
            glib::timeout_add_local(Duration::from_millis(25), move || {
                match receiver.try_recv() {
                    Ok(verified) => {
                        if let Ok(mut state) = state.lock() {
                            state.complete_focus(verified);
                        }
                        if verified {
                            focused.store(true, Ordering::Release);
                            close_all(&windows);
                            gtk::main_quit();
                        } else {
                            show_all(&windows);
                            label.set_label("Thử chuyển lại");
                            label.set_sensitive(true);
                        }
                        ControlFlow::Break
                    }
                    Err(mpsc::TryRecvError::Empty) => ControlFlow::Continue,
                    Err(mpsc::TryRecvError::Disconnected) => {
                        if let Ok(mut state) = state.lock() {
                            state.complete_focus(false);
                        }
                        show_all(&windows);
                        label.set_label("Thử chuyển lại");
                        label.set_sensitive(true);
                        ControlFlow::Break
                    }
                }
            });
        });

        let state_for_dismiss = Arc::clone(&state);
        let windows_for_dismiss = Rc::clone(&windows);
        dismiss_button.connect_clicked(move |_| {
            if let Ok(mut state) = state_for_dismiss.lock() {
                state.dismiss();
            }
            close_all(&windows_for_dismiss);
            gtk::main_quit();
        });
        let state_for_close = Arc::clone(&state);
        window.connect_delete_event(move |_, _| {
            if let Ok(mut state) = state_for_close.lock() {
                state.dismiss();
            }
            gtk::main_quit();
            glib::Propagation::Proceed
        });
        window.show_all();
        if let Some(monitor) = monitor {
            window.move_(
                monitor.work_right.saturating_sub(400),
                monitor.work_top.saturating_add(24),
            );
        }
        windows.borrow_mut().push(window);
    }

    let (sender, receiver) = mpsc::sync_channel(1);
    let probe_running = Arc::new(AtomicBool::new(false));
    let state_for_timer = Arc::clone(&state);
    let windows_for_timer = Rc::clone(&windows);
    let active_probe = Arc::clone(&active);
    let delay = request.auto_dismiss_delay;
    let explicit_timeout = request.timeout;
    glib::timeout_add_local(Duration::from_millis(100), move || {
        if let Ok(active_now) = receiver.try_recv() {
            probe_running.store(false, Ordering::Release);
            if state_for_timer.lock().is_ok_and(|mut state| {
                state.poll_active(started.elapsed().as_secs_f64(), active_now, delay)
            }) {
                close_all(&windows_for_timer);
                gtk::main_quit();
                return ControlFlow::Break;
            }
        }
        if explicit_timeout > 0 && started.elapsed() >= Duration::from_secs(explicit_timeout) {
            if let Ok(mut state) = state_for_timer.lock() {
                state.dismiss();
            }
            close_all(&windows_for_timer);
            gtk::main_quit();
            return ControlFlow::Break;
        }
        if !probe_running.swap(true, Ordering::AcqRel) {
            let sender = sender.clone();
            let probe = Arc::clone(&active_probe);
            let _ = std::thread::Builder::new()
                .name("anoti-ui-active".to_owned())
                .spawn(move || {
                    let _ = sender.send(probe());
                });
        }
        ControlFlow::Continue
    });

    gtk::main();
    let state = state
        .lock()
        .map_err(|_| PlatformError::Operation("GTK overlay state lock poisoned".to_owned()))?;
    Ok(OverlayOutcome {
        displayed: true,
        dismissed: state.dismissed,
        focused: focused.load(Ordering::Acquire),
    })
}

fn close_all(windows: &Rc<RefCell<Vec<gtk::Window>>>) {
    for window in windows.borrow().iter() {
        window.close();
    }
}

fn hide_all(windows: &Rc<RefCell<Vec<gtk::Window>>>) {
    for window in windows.borrow().iter() {
        window.hide();
    }
}

fn show_all(windows: &Rc<RefCell<Vec<gtk::Window>>>) {
    for window in windows.borrow().iter() {
        window.show_all();
    }
}

fn point_in_widget<W: IsA<gtk::Widget>>(widget: &W, window: &gtk::Window, x: f64, y: f64) -> bool {
    let Some((left, top)) = widget.translate_coordinates(window, 0, 0) else {
        return false;
    };
    let allocation = widget.allocation();
    let right = left.saturating_add(allocation.width());
    let bottom = top.saturating_add(allocation.height());
    x >= f64::from(left) && x < f64::from(right) && y >= f64::from(top) && y < f64::from(bottom)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[ignore = "requires a live Linux X display and an external background click"]
    fn native_background_click_dismisses_overlay() {
        let request = NotificationRequest {
            app_name: "Smoke".to_owned(),
            title: "Background click".to_owned(),
            message: "Clicking the popup background must dismiss it".to_owned(),
            timeout: 10,
            ..NotificationRequest::default()
        };
        let outcome = show_overlay(&request, &[], Arc::new(|| false), Arc::new(|| false))
            .expect("GTK overlay should initialize on the test display");
        assert!(outcome.dismissed);
        assert!(!outcome.focused);
    }
}
