use std::collections::HashSet;
use std::fs;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProcStat {
    pub pid: u32,
    pub command: String,
    pub state: char,
    pub parent_pid: u32,
    pub process_group: i32,
    pub session: i32,
    pub tty_number: i32,
    pub foreground_group: i32,
}

pub fn parse_proc_stat(input: &str) -> Option<ProcStat> {
    let open = input.find('(')?;
    let close = input.rfind(") ")?;
    if close <= open {
        return None;
    }
    let pid = input[..open].trim().parse().ok()?;
    let command = input[open + 1..close].to_owned();
    let fields = input[close + 2..].split_whitespace().collect::<Vec<_>>();
    Some(ProcStat {
        pid,
        command,
        state: fields.first()?.chars().next()?,
        parent_pid: fields.get(1)?.parse().ok()?,
        process_group: fields.get(2)?.parse().ok()?,
        session: fields.get(3)?.parse().ok()?,
        tty_number: fields.get(4)?.parse().ok()?,
        foreground_group: fields.get(5)?.parse().ok()?,
    })
}

pub fn process_ancestry(start_pid: u32) -> Vec<u32> {
    let mut chain = Vec::new();
    let mut seen = HashSet::new();
    let mut current = start_pid;
    while current > 1 && chain.len() < 32 && seen.insert(current) {
        chain.push(current);
        current = fs::read_to_string(format!("/proc/{current}/stat"))
            .ok()
            .and_then(|stat| parse_proc_stat(&stat))
            .map_or(0, |stat| stat.parent_pid);
    }
    chain
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn proc_stat_parser_handles_spaces_and_closing_parenthesis() {
        let stat = parse_proc_stat("123 (agent ) worker) S 42 10 10 34817 10 0 0").unwrap();
        assert_eq!(stat.pid, 123);
        assert_eq!(stat.command, "agent ) worker");
        assert_eq!(stat.parent_pid, 42);
        assert_eq!(stat.process_group, 10);
        assert_eq!(stat.tty_number, 34817);
        assert_eq!(stat.foreground_group, 10);
    }

    #[test]
    fn malformed_proc_stat_is_rejected() {
        assert!(parse_proc_stat("broken").is_none());
        assert!(parse_proc_stat("1 (name) S").is_none());
    }
}
