import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

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
  </interface>
</node>`;

const DEVELOPER_CLASSES = [
    'code', 'vscodium', 'cursor', 'windsurf', 'antigravity', 'zed',
    'gnome-terminal', 'tilix', 'alacritty', 'kitty', 'wezterm', 'konsole',
    'ptyxis', 'kgx', 'pycharm', 'idea', 'clion', 'webstorm', 'goland',
    'phpstorm', 'rider', 'rubymine', 'datagrip', 'fleet', 'sublime_text',
    'gedit', 'kate', 'emacs', 'neovim', 'gvim',
];

function normalize(value) {
    return String(value ?? '').trim().toLowerCase().replace(/\s+/g, ' ');
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
        const text = new TextDecoder().decode(contents);
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
    const wmClass = normalize(window.get_wm_class?.());
    const appId = normalize(window.get_gtk_application_id?.());
    return DEVELOPER_CLASSES.some(item => wmClass.includes(item) || appId.includes(item));
}

function isSupportedWindow(window) {
    if (!window || window.is_skip_taskbar())
        return false;
    const windowType = window.get_window_type();
    return windowType === Meta.WindowType.NORMAL ||
        windowType === Meta.WindowType.DIALOG ||
        windowType === Meta.WindowType.MODAL_DIALOG;
}

function matchesTarget(window, callerPid, projectHint, titleFingerprint) {
    if (!isSupportedWindow(window) || !isDeveloperWindow(window))
        return false;

    const title = normalize(window.get_title());
    const hint = normalize(projectHint);
    const titleMatches = Boolean(hint && title.includes(hint)) ||
        titlesCompatible(titleFingerprint, title);
    const pidMatches = isPidInAncestry(window.get_pid(), callerPid);

    if (pidMatches)
        return hint || titleFingerprint ? titleMatches : true;
    return titleMatches;
}

export default class AiAgentNotifierWindowFocus extends Extension {
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

    FocusWindow(callerPid, projectHint, titleFingerprint) {
        const candidates = global.get_window_actors()
            .map(actor => actor.get_meta_window())
            .filter(window => matchesTarget(window, callerPid, projectHint, titleFingerprint));

        if (candidates.length !== 1)
            return false;

        Main.activateWindow(candidates[0], global.get_current_time());
        return true;
    }
}
