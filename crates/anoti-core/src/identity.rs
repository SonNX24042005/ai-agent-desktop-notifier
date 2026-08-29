//! Deterministic, platform-neutral window identity resolution.

use std::collections::HashSet;

use crate::{FocusOutcome, WindowIdentity};

/// Evidence collected by a platform backend for one visible top-level window.
#[allow(clippy::struct_excessive_bools)]
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CandidateEvidence {
    pub exact_instance_match: bool,
    pub session_match: bool,
    pub pid_match: bool,
    pub project_match: bool,
    pub direct_id_match: bool,
    pub app_match: bool,
    pub title_match: bool,
    pub stale: bool,
    pub developer_window: bool,
}

/// A platform window plus normalized evidence used by the shared resolver.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct WindowCandidate {
    pub id: String,
    pub instance_id: String,
    pub pid: u32,
    pub title: String,
    pub app_id: String,
    pub generation: u64,
    pub evidence: CandidateEvidence,
}

/// Generates a random non-reusable unique instance identifier for a concrete window lifetime.
#[must_use]
pub fn generate_window_instance_id() -> String {
    uuid::Uuid::new_v4().to_string()
}

/// Removes spinner/icon prefixes and normalizes case and whitespace.
#[must_use]
pub fn normalize_title(value: &str) -> String {
    let trimmed = value.trim().to_lowercase();
    let first_word = trimmed
        .char_indices()
        .find(|(_, character)| character.is_alphanumeric() || *character == '_')
        .map_or(trimmed.len(), |(index, _)| index);
    trimmed[first_word..]
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

/// Preserves legacy compatibility: an empty fingerprint does not reject a candidate.
#[must_use]
pub fn titles_compatible(expected: &str, current: &str) -> bool {
    let expected = normalize_title(expected);
    let current = normalize_title(current);
    expected.is_empty()
        || current.is_empty()
        || expected == current
        || expected.contains(&current)
        || current.contains(&expected)
}

/// Normalizes a PID snapshot without zeros or duplicates while preserving order.
#[must_use]
pub fn normalize_pid_chain<I>(values: I, caller_pid: u32) -> Vec<u32>
where
    I: IntoIterator<Item = u32>,
{
    let mut seen = HashSet::new();
    std::iter::once(caller_pid)
        .chain(values)
        .filter(|pid| *pid > 1 && seen.insert(*pid))
        .take(32)
        .collect()
}

fn score(candidate: &WindowCandidate) -> Option<u8> {
    let evidence = &candidate.evidence;
    if evidence.stale || !evidence.developer_window {
        return None;
    }
    if evidence.exact_instance_match {
        return Some(7);
    }
    if evidence.session_match {
        return Some(6);
    }
    if evidence.pid_match && (evidence.project_match || evidence.title_match) {
        return Some(5);
    }
    if evidence.pid_match {
        return Some(4);
    }
    if evidence.project_match && evidence.app_match {
        return Some(3);
    }
    if evidence.project_match || evidence.title_match {
        return Some(2);
    }
    if evidence.direct_id_match && evidence.app_match {
        return Some(1);
    }
    None
}

/// Selects only a unique candidate at the strongest evidence tier.
pub fn resolve_candidate(
    candidates: &[WindowCandidate],
) -> Result<Option<&WindowCandidate>, FocusOutcome> {
    let scored = candidates
        .iter()
        .filter_map(|candidate| score(candidate).map(|strength| (strength, candidate)))
        .collect::<Vec<_>>();
    let Some(best_strength) = scored.iter().map(|(strength, _)| *strength).max() else {
        return Ok(None);
    };
    let mut best = scored
        .iter()
        .filter(|(strength, _)| *strength == best_strength)
        .map(|(_, candidate)| *candidate);
    let selected = best.next().expect("best strength requires one candidate");
    if best.next().is_some() {
        Err(FocusOutcome::Ambiguous)
    } else {
        Ok(Some(selected))
    }
}

/// Creates a query identity with a stable PID chain.
#[must_use]
pub fn normalized_identity(mut identity: WindowIdentity) -> WindowIdentity {
    identity.caller_pid_chain = normalize_pid_chain(identity.caller_pid_chain, identity.caller_pid);
    identity
}

#[cfg(test)]
mod tests {
    use super::*;

    fn candidate(id: &str, evidence: CandidateEvidence) -> WindowCandidate {
        WindowCandidate {
            id: id.to_owned(),
            instance_id: format!("test:{id}:100"),
            evidence,
            ..WindowCandidate::default()
        }
    }

    #[test]
    fn normalize_title_removes_spinner_and_whitespace() {
        assert_eq!(
            normalize_title("⠋  Claude Code  - Project"),
            "claude code - project"
        );
    }

    #[test]
    fn pid_chain_is_stable_and_unique() {
        assert_eq!(
            normalize_pid_chain([900, 800, 900, 0, 700], 900),
            [900, 800, 700]
        );
    }

    #[test]
    fn stronger_unique_candidate_wins() {
        let weak = candidate(
            "1",
            CandidateEvidence {
                project_match: true,
                developer_window: true,
                ..CandidateEvidence::default()
            },
        );
        let strong = candidate(
            "2",
            CandidateEvidence {
                pid_match: true,
                developer_window: true,
                ..CandidateEvidence::default()
            },
        );
        assert_eq!(resolve_candidate(&[weak, strong]).unwrap().unwrap().id, "2");
    }

    #[test]
    fn ambiguity_at_best_tier_is_rejected() {
        let evidence = CandidateEvidence {
            pid_match: true,
            developer_window: true,
            ..CandidateEvidence::default()
        };
        assert_eq!(
            resolve_candidate(&[candidate("1", evidence.clone()), candidate("2", evidence)]),
            Err(FocusOutcome::Ambiguous)
        );
    }

    #[test]
    fn stale_and_non_developer_candidates_are_rejected() {
        let stale = candidate(
            "1",
            CandidateEvidence {
                session_match: true,
                stale: true,
                developer_window: true,
                ..CandidateEvidence::default()
            },
        );
        let unrelated = candidate(
            "2",
            CandidateEvidence {
                pid_match: true,
                developer_window: false,
                ..CandidateEvidence::default()
            },
        );
        assert!(resolve_candidate(&[stale, unrelated]).unwrap().is_none());
    }

    #[test]
    fn pid_chain_is_bounded_to_thirty_two_entries() {
        let chain = normalize_pid_chain(2..100, 99);
        assert_eq!(chain.len(), 32);
        assert_eq!(chain[0], 99);
    }

    #[test]
    fn exact_instance_match_overrides_pid_and_title_ambiguity() {
        let ambiguous1 = candidate(
            "1",
            CandidateEvidence {
                pid_match: true,
                title_match: true,
                developer_window: true,
                ..CandidateEvidence::default()
            },
        );
        let mut ambiguous2 = candidate(
            "2",
            CandidateEvidence {
                exact_instance_match: true,
                pid_match: true,
                title_match: true,
                developer_window: true,
                ..CandidateEvidence::default()
            },
        );
        ambiguous2.instance_id = "test:2:100".to_owned();

        // Exact instance match has score 7, beating general pid+title match (score 5)
        let candidates = [ambiguous1, ambiguous2];
        let resolved = resolve_candidate(&candidates).unwrap().unwrap();
        assert_eq!(resolved.id, "2");
        assert_eq!(resolved.instance_id, "test:2:100");
    }

    #[test]
    fn generate_window_instance_id_produces_unique_uuids() {
        let instance1 = generate_window_instance_id();
        let instance2 = generate_window_instance_id();
        assert_ne!(instance1, instance2);
        assert_eq!(instance1.len(), 36);
        assert_eq!(instance2.len(), 36);
        assert!(uuid::Uuid::parse_str(&instance1).is_ok());
        assert!(uuid::Uuid::parse_str(&instance2).is_ok());
    }
}
