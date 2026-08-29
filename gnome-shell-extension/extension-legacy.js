const {Gio, GLib, Meta} = imports.gi;
const Main = imports.ui.main;

const BUS_NAME = 'io.github.sonnx24042005.AiAgentNotifier';
const OBJECT_PATH = '/io/github/sonnx24042005/AiAgentNotifier';
const INTERFACE_XML = `
<node>
  <interface name="io.github.sonnx24042005.AiAgentNotifier">
    <method name="FocusWindow">
      <arg type="u" name="callerPid" direction="in"/>
      <arg type="s" name="projectHint" direction="in"/>
      <arg type="s" name="titleFingerprint" direction="in"/>
      <arg type="b" name="focused" direction="out"/>
    </method>
    <method name="FocusWindowV2">
      <arg type="u" name="callerPid" direction="in"/>
      <arg type="s" name="projectHint" direction="in"/>
      <arg type="s" name="titleFingerprint" direction="in"/>
      <arg type="s" name="appHint" direction="in"/>
      <arg type="b" name="focused" direction="out"/>
    </method>
    <method name="IsWindowActive">
      <arg type="u" name="callerPid" direction="in"/>
      <arg type="s" name="projectHint" direction="in"/>
      <arg type="s" name="titleFingerprint" direction="in"/>
      <arg type="s" name="appHint" direction="in"/>
      <arg type="b" name="active" direction="out"/>
    </method>
    <method name="GetContractVersion">
      <arg type="u" name="version" direction="out"/>
    </method>
    <method name="FocusWindowV3">
      <arg type="au" name="callerPidChain" direction="in"/>
      <arg type="s" name="projectHint" direction="in"/>
      <arg type="s" name="titleFingerprint" direction="in"/>
      <arg type="s" name="appHint" direction="in"/>
      <arg type="b" name="focused" direction="out"/>
    </method>
    <method name="IsWindowActiveV3">
      <arg type="au" name="callerPidChain" direction="in"/>
      <arg type="s" name="projectHint" direction="in"/>
      <arg type="s" name="titleFingerprint" direction="in"/>
      <arg type="s" name="appHint" direction="in"/>
      <arg type="b" name="active" direction="out"/>
    </method>
    <method name="FocusWindowV4">
      <arg type="s" name="windowToken" direction="in"/>
      <arg type="u" name="windowPid" direction="in"/>
      <arg type="au" name="callerPidChain" direction="in"/>
      <arg type="s" name="projectHint" direction="in"/>
      <arg type="s" name="titleFingerprint" direction="in"/>
      <arg type="s" name="appHint" direction="in"/>
      <arg type="b" name="focused" direction="out"/>
    </method>
    <method name="IsWindowActiveV4">
      <arg type="s" name="windowToken" direction="in"/>
      <arg type="u" name="windowPid" direction="in"/>
      <arg type="au" name="callerPidChain" direction="in"/>
      <arg type="s" name="projectHint" direction="in"/>
      <arg type="s" name="titleFingerprint" direction="in"/>
      <arg type="s" name="appHint" direction="in"/>
      <arg type="b" name="active" direction="out"/>
    </method>
    <method name="CaptureActiveWindowV3">
      <arg type="au" name="callerPidChain" direction="in"/>
      <arg type="s" name="projectHint" direction="in"/>
      <arg type="s" name="appHint" direction="in"/>
      <arg type="b" name="captured" direction="out"/>
      <arg type="s" name="windowToken" direction="out"/>
      <arg type="u" name="windowPid" direction="out"/>
      <arg type="s" name="title" direction="out"/>
      <arg type="s" name="appId" direction="out"/>
    </method>
    <method name="CaptureWindowByTitleV5">
      <arg type="s" name="titleMarker" direction="in"/>
      <arg type="b" name="captured" direction="out"/>
      <arg type="s" name="windowToken" direction="out"/>
      <arg type="u" name="windowPid" direction="out"/>
      <arg type="s" name="title" direction="out"/>
      <arg type="s" name="appId" direction="out"/>
    </method>
    <method name="KeepOverlayAboveV6">
      <arg type="u" name="overlayPid" direction="in"/>
      <arg type="b" name="promoted" direction="out"/>
    </method>
  </interface>
</node>`;

const DEVELOPER_CLASSES = [
    'code', 'vscodium', 'cursor', 'windsurf', 'antigravity', 'zed',
    'chatgpt', 'codex',
    'gnome-terminal', 'tilix', 'alacritty', 'kitty', 'wezterm', 'konsole',
    'ptyxis', 'kgx', 'pycharm', 'idea', 'clion', 'webstorm', 'goland',
    'phpstorm', 'rider', 'rubymine', 'datagrip', 'fleet', 'sublime_text',
    'gedit', 'kate', 'emacs', 'neovim', 'gvim',
];

const OVERLAY_WINDOW_TITLE = 'AI agent notifier';

function normalize(value) {
    return String(value || '').trim().toLowerCase().replace(/\s+/g, ' ');
}

function canonicalAppIdentity(value) {
    return normalize(value).replace(/[^a-z0-9]+/g, '-');
}

function titlesCompatible(expected, current) {
    const expectedTitle = normalize(expected);
    const currentTitle = normalize(current);
    if (!expectedTitle || !currentTitle)
        return false;
    return expectedTitle === currentTitle ||
        expectedTitle.includes(currentTitle) || currentTitle.includes(expectedTitle);
}

function readParentPid(pid) {
    try {
        const [ok, contents] = GLib.file_get_contents(`/proc/${pid}/status`);
        if (!ok)
            return 0;
        const text = imports.byteArray.toString(contents);
        const match = text.match(/^PPid:\s+(\d+)$/m);
        return match ? Number.parseInt(match[1], 10) : 0;
    } catch (_error) {
        return 0;
    }
}

function isPidInAncestry(targetPid, startPid) {
    const target = Number(targetPid) || 0;
    let current = Number(startPid) || 0;
    const seen = new Set();
    while (current > 1 && !seen.has(current) && seen.size < 32) {
        if (current === target)
            return true;
        seen.add(current);
        current = readParentPid(current);
    }
    return false;
}

function isDeveloperWindow(window) {
    const wmClass = canonicalAppIdentity(window.get_wm_class());
    let appId = '';
    try {
        appId = canonicalAppIdentity(window.get_gtk_application_id());
    } catch (_error) {
        appId = '';
    }
    return DEVELOPER_CLASSES.some(item => {
        const candidate = canonicalAppIdentity(item);
        return wmClass.includes(candidate) || appId.includes(candidate);
    });
}

function isSupportedWindow(window) {
    if (!window || window.is_skip_taskbar())
        return false;
    const windowType = window.get_window_type();
    return windowType === Meta.WindowType.NORMAL ||
        windowType === Meta.WindowType.DIALOG ||
        windowType === Meta.WindowType.MODAL_DIALOG;
}

function appIdentity(window) {
    let appId = '';
    try {
        appId = canonicalAppIdentity(window.get_gtk_application_id());
    } catch (_error) {
        appId = '';
    }
    return `${canonicalAppIdentity(window.get_wm_class())} ${appId}`;
}

function matchesAppHint(window, appHint) {
    const sourceApp = canonicalAppIdentity(appHint);
    const identity = appIdentity(window);
    if (sourceApp === 'antigravity')
        return identity.includes('antigravity');
    if (sourceApp === 'codex')
        return ['chatgpt', 'codex']
            .some(item => identity.includes(item));
    return false;
}

function targetMatchStrength(window, callerPid, projectHint, titleFingerprint, appHint) {
    if (!isSupportedWindow(window) || !isDeveloperWindow(window))
        return 0;

    const title = normalize(window.get_title());
    const hint = normalize(projectHint);
    const titleMatches = Boolean(hint && title.includes(hint)) ||
        titlesCompatible(titleFingerprint, title);
    const pidMatches = isPidInAncestry(window.get_pid(), callerPid);

    if (pidMatches && titleMatches)
        return 4;
    if (pidMatches)
        return 3;
    if (titleMatches)
        return 2;
    return matchesAppHint(window, appHint) ? 1 : 0;
}

function selectTargetWindow(callerPid, projectHint, titleFingerprint, appHint) {
    const scoredCandidates = global.get_window_actors()
        .map(actor => actor.get_meta_window())
        .map(window => ({
            window,
            strength: targetMatchStrength(window, callerPid, projectHint, titleFingerprint, appHint),
        }))
        .filter(candidate => candidate.strength > 0);

    const bestStrength = scoredCandidates.reduce(
        (best, candidate) => Math.max(best, candidate.strength),
        0
    );
    const candidates = scoredCandidates.filter(candidate => candidate.strength === bestStrength);
    return candidates.length === 1 ? candidates[0].window : null;
}

function selectTargetWindowV3(callerPidChain, projectHint, titleFingerprint, appHint) {
    const pids = Array.from(callerPidChain || []).filter(pid => Number(pid) > 1);
    const scoredCandidates = global.get_window_actors()
        .map(actor => actor.get_meta_window())
        .map(window => ({
            window,
            strength: pids.reduce(
                (best, pid) => Math.max(
                    best,
                    targetMatchStrength(window, pid, projectHint, titleFingerprint, appHint)
                ),
                targetMatchStrength(window, 0, projectHint, titleFingerprint, appHint)
            ),
        }))
        .filter(candidate => candidate.strength > 0);
    const bestStrength = scoredCandidates.reduce(
        (best, candidate) => Math.max(best, candidate.strength),
        0
    );
    const candidates = scoredCandidates.filter(candidate => candidate.strength === bestStrength);
    return candidates.length === 1 ? candidates[0].window : null;
}

function windowToken(window) {
    return `wayland:${window.get_stable_sequence()}`;
}

function keepOverlayAbove(overlayPid) {
    const pid = Number(overlayPid) || 0;
    if (pid <= 1)
        return false;
    const windows = global.get_window_actors()
        .map(actor => actor.get_meta_window())
        .filter(window => window.get_pid() === pid)
        .filter(window => String(window.get_title() || '') === OVERLAY_WINDOW_TITLE);
    if (windows.length === 0)
        return false;
    for (const window of windows) {
        window.make_above();
        window.stick();
    }
    return true;
}

function captureWindowByTitleMarker(titleMarker) {
    const marker = String(titleMarker || '').trim();
    if (!marker.startsWith('anoti-capture-') || marker.length > 100)
        return [false, '', 0, '', ''];
    const candidates = global.get_window_actors()
        .map(actor => actor.get_meta_window())
        .filter(window => isSupportedWindow(window) && isDeveloperWindow(window))
        .filter(window => String(window.get_title() || '').includes(marker));
    if (candidates.length !== 1)
        return [false, '', 0, '', ''];
    const targetWindow = candidates[0];
    return [
        true,
        windowToken(targetWindow),
        targetWindow.get_pid(),
        String(targetWindow.get_title() || ''),
        appIdentity(targetWindow),
    ];
}

function selectTargetWindowV4(
    requestedToken, windowPid, callerPidChain, projectHint, titleFingerprint, appHint
) {
    const token = String(requestedToken || '').trim();
    if (!token)
        return selectTargetWindowV3(callerPidChain, projectHint, titleFingerprint, appHint);

    const candidates = global.get_window_actors()
        .map(actor => actor.get_meta_window())
        .filter(window => isSupportedWindow(window) && isDeveloperWindow(window))
        .filter(window => windowToken(window) === token);
    if (candidates.length !== 1)
        return null;

    const targetWindow = candidates[0];
    const expectedPid = Number(windowPid) || 0;
    if (expectedPid > 1)
        return targetWindow.get_pid() === expectedPid ? targetWindow : null;

    const expectedTitle = String(titleFingerprint || '').trim();
    return expectedTitle && titlesCompatible(expectedTitle, targetWindow.get_title())
        ? targetWindow
        : null;
}

class AiAgentNotifierWindowFocus {
    enable() {
        this._ownerId = Gio.bus_own_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            connection => {
                this._dbusObject = Gio.DBusExportedObject.wrapJSObject(INTERFACE_XML, this);
                this._dbusObject.export(connection, OBJECT_PATH);
            },
            null,
            null
        );
    }

    disable() {
        if (this._dbusObject) {
            this._dbusObject.flush();
            this._dbusObject.unexport();
            this._dbusObject = null;
        }
        if (this._ownerId) {
            Gio.bus_unown_name(this._ownerId);
            this._ownerId = 0;
        }
    }

    _focusWindow(callerPid, projectHint, titleFingerprint, appHint) {
        const targetWindow = selectTargetWindow(callerPid, projectHint, titleFingerprint, appHint);
        if (!targetWindow)
            return false;

        Main.activateWindow(targetWindow, global.get_current_time());
        return true;
    }

    FocusWindow(callerPid, projectHint, titleFingerprint) {
        return this._focusWindow(callerPid, projectHint, titleFingerprint, '');
    }

    FocusWindowV2(callerPid, projectHint, titleFingerprint, appHint) {
        return this._focusWindow(callerPid, projectHint, titleFingerprint, appHint);
    }

    GetContractVersion() {
        return 6;
    }

    FocusWindowV3(callerPidChain, projectHint, titleFingerprint, appHint) {
        const targetWindow = selectTargetWindowV3(
            callerPidChain, projectHint, titleFingerprint, appHint
        );
        if (!targetWindow)
            return false;
        Main.activateWindow(targetWindow, global.get_current_time());
        return true;
    }

    FocusWindowV4(windowTokenValue, windowPid, callerPidChain, projectHint, titleFingerprint, appHint) {
        const targetWindow = selectTargetWindowV4(
            windowTokenValue, windowPid, callerPidChain, projectHint, titleFingerprint, appHint
        );
        if (!targetWindow)
            return false;
        Main.activateWindow(targetWindow, global.get_current_time());
        return true;
    }

    IsWindowActive(callerPid, projectHint, titleFingerprint, appHint) {
        let activeWindow = null;
        try {
            activeWindow = global.display.get_focus_window();
        } catch (_error) {
            activeWindow = global.display.focus_window || null;
        }
        const targetWindow = selectTargetWindow(callerPid, projectHint, titleFingerprint, appHint);
        return Boolean(activeWindow && targetWindow && activeWindow === targetWindow);
    }

    IsWindowActiveV3(callerPidChain, projectHint, titleFingerprint, appHint) {
        let activeWindow = null;
        try {
            activeWindow = global.display.get_focus_window();
        } catch (_error) {
            activeWindow = global.display.focus_window || null;
        }
        const targetWindow = selectTargetWindowV3(
            callerPidChain, projectHint, titleFingerprint, appHint
        );
        return Boolean(activeWindow && targetWindow && activeWindow === targetWindow);
    }

    IsWindowActiveV4(windowTokenValue, windowPid, callerPidChain, projectHint, titleFingerprint, appHint) {
        let activeWindow = null;
        try {
            activeWindow = global.display.get_focus_window();
        } catch (_error) {
            activeWindow = global.display.focus_window || null;
        }
        const targetWindow = selectTargetWindowV4(
            windowTokenValue, windowPid, callerPidChain, projectHint, titleFingerprint, appHint
        );
        return Boolean(activeWindow && targetWindow && activeWindow === targetWindow);
    }

    CaptureActiveWindowV3(callerPidChain, projectHint, appHint) {
        let activeWindow = null;
        try {
            activeWindow = global.display.get_focus_window();
        } catch (_error) {
            activeWindow = global.display.focus_window || null;
        }
        if (!activeWindow || !isSupportedWindow(activeWindow) || !isDeveloperWindow(activeWindow))
            return [false, '', 0, '', ''];
        const matched = Array.from(callerPidChain || []).some(pid =>
            isPidInAncestry(activeWindow.get_pid(), pid)
        );
        const title = String(activeWindow.get_title() || '');
        const hint = normalize(projectHint);
        if (!matched && !(hint && normalize(title).includes(hint)))
            return [false, '', 0, '', ''];
        const token = windowToken(activeWindow);
        return [true, token, activeWindow.get_pid(), title, appIdentity(activeWindow) || appHint];
    }

    CaptureWindowByTitleV5(titleMarker) {
        return captureWindowByTitleMarker(titleMarker);
    }

    KeepOverlayAboveV6(overlayPid) {
        return keepOverlayAbove(overlayPid);
    }
}

function init() {
    return new AiAgentNotifierWindowFocus();
}
