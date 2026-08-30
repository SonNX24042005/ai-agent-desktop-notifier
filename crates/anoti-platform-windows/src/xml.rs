//! Toast XML construction and URI escaping utilities.

use std::path::Path;

/// Escapes XML special characters for inclusion in XML text nodes or attribute values.
#[must_use]
pub fn xml_escape(value: &str) -> String {
    let mut escaped = String::with_capacity(value.len());
    for ch in value.chars() {
        match ch {
            '&' => escaped.push_str("&amp;"),
            '<' => escaped.push_str("&lt;"),
            '>' => escaped.push_str("&gt;"),
            '"' => escaped.push_str("&quot;"),
            '\'' => escaped.push_str("&apos;"),
            other => escaped.push(other),
        }
    }
    escaped
}

/// Converts a file path into a valid file:/// URI with percent-encoding.
#[must_use]
pub fn to_file_uri(path: &Path) -> String {
    let raw = path.to_string_lossy();
    let normalized = raw.replace('\\', "/");
    let (prefix, body) = if let Some(stripped) = normalized.strip_prefix("//") {
        ("file://", stripped)
    } else if let Some(stripped) = normalized.strip_prefix('/') {
        ("file:///", stripped)
    } else {
        ("file:///", normalized.as_str())
    };

    let mut encoded = String::from(prefix);
    for byte in body.bytes() {
        match byte {
            b'a'..=b'z' | b'A'..=b'Z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' | b'/' | b':' => {
                encoded.push(byte as char);
            }
            other => {
                use std::fmt::Write;
                let _ = write!(encoded, "%{other:02X}");
            }
        }
    }
    encoded
}

/// Builds the `ToastGeneric` XML payload with title, message, and optional `appLogoOverride`.
#[must_use]
pub fn build_toast_xml(title: &str, message: &str, icon_uri: Option<&str>) -> String {
    let image_tag = match icon_uri {
        Some(uri) if !uri.is_empty() => {
            format!(
                "<image placement=\"appLogoOverride\" src=\"{}\" hint-crop=\"circle\"/>",
                xml_escape(uri)
            )
        }
        _ => String::new(),
    };
    format!(
        "<toast><visual><binding template=\"ToastGeneric\"><text>{}</text><text>{}</text>{}</binding></visual></toast>",
        xml_escape(title),
        xml_escape(message),
        image_tag,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn xml_escape_handles_special_characters() {
        assert_eq!(
            xml_escape("<Hello & \"World\" '123'>"),
            "&lt;Hello &amp; &quot;World&quot; &apos;123&apos;&gt;"
        );
    }

    #[test]
    fn to_file_uri_formats_windows_paths_with_spaces_and_special_chars() {
        let path = Path::new(r"C:\Users\John Doe\.local\share\anoti\icons\claude.png");
        assert_eq!(
            to_file_uri(path),
            "file:///C:/Users/John%20Doe/.local/share/anoti/icons/claude.png"
        );

        let special = Path::new(r"C:\My Projects\A&B #1 (Beta)\icon.png");
        assert_eq!(
            to_file_uri(special),
            "file:///C:/My%20Projects/A%26B%20%231%20%28Beta%29/icon.png"
        );

        let unix_path = Path::new("/home/user/with spaces/anoti.png");
        assert_eq!(
            to_file_uri(unix_path),
            "file:///home/user/with%20spaces/anoti.png"
        );
    }

    #[test]
    fn build_toast_xml_constructs_valid_payload_with_escaped_uri() {
        let xml = build_toast_xml(
            "Agent & System",
            "Turn complete <ok>",
            Some("file:///C:/Path%20With%20Spaces/icon.png"),
        );
        assert_eq!(
            xml,
            "<toast><visual><binding template=\"ToastGeneric\"><text>Agent &amp; System</text><text>Turn complete &lt;ok&gt;</text><image placement=\"appLogoOverride\" src=\"file:///C:/Path%20With%20Spaces/icon.png\" hint-crop=\"circle\"/></binding></visual></toast>"
        );

        let xml_no_icon = build_toast_xml("Title", "Message", None);
        assert_eq!(
            xml_no_icon,
            "<toast><visual><binding template=\"ToastGeneric\"><text>Title</text><text>Message</text></binding></visual></toast>"
        );
    }
}
