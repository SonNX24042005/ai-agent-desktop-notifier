fn main() {
    if let Err(error) = anoti_app::run() {
        eprintln!("anoti: {error}");
        std::process::exit(1);
    }
}
