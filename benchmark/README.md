# Controlled Benchmark

The 20 deterministic cases live in `cases.json` and are loaded/persisted into PostgreSQL at application startup. Each case records language, category, severity, expected finding types and a fixed Validation/Holdout split. The benchmark measures the Provider detection layer; the full Runtime path is covered by the API and Compose smoke tests.
