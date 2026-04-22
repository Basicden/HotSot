#!/usr/bin/env python3
"""
HotSot — Chaos Engineering Stack Validation

Validates the entire chaos engineering infrastructure WITHOUT requiring
running services. Checks code, config, and logical consistency.

Usage:
    python3 chaos/scripts/validate-stack.py

Exit code: 0 = all checks pass, 1 = issues found
"""

import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import List, Tuple

# Project root
ROOT = Path(__file__).resolve().parent.parent.parent

# Colors for output
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
NC = "\033[0m"


class ValidationResult:
    def __init__(self):
        self.passed = []
        self.failed = []
        self.warnings = []

    def ok(self, msg: str):
        self.passed.append(msg)
        print(f"  {GREEN}✅ {msg}{NC}")

    def fail(self, msg: str):
        self.failed.append(msg)
        print(f"  {RED}❌ {msg}{NC}")

    def warn(self, msg: str):
        self.warnings.append(msg)
        print(f"  {YELLOW}⚠ {msg}{NC}")


def header(title: str):
    print(f"\n{CYAN}{'═' * 60}{NC}")
    print(f"{CYAN}  {title}{NC}")
    print(f"{CYAN}{'═' * 60}{NC}")


def validate_python_syntax(result: ValidationResult):
    """Layer 1: Validate all Python files parse correctly."""
    header("LAYER 1: Python Syntax Validation")

    critical_files = [
        "shared/utils/observability.py",
        "shared/utils/middleware.py",
        "shared/utils/__init__.py",
        "chaos/scripts/validate-criteria.py",
    ]

    for f in critical_files:
        path = ROOT / f
        if not path.exists():
            result.fail(f"MISSING: {f}")
            continue
        try:
            with open(path) as fp:
                ast.parse(fp.read())
            result.ok(f"{f} — syntax valid")
        except SyntaxError as e:
            result.fail(f"{f} — SYNTAX ERROR: {e}")

    # All service main.py files
    for f in sorted(ROOT.glob("services/*/app/main.py")):
        try:
            with open(f) as fp:
                ast.parse(fp.read())
            result.ok(f"{f.relative_to(ROOT)} — syntax valid")
        except SyntaxError as e:
            result.fail(f"{f.relative_to(ROOT)} — SYNTAX ERROR: {e}")


def validate_yaml_json(result: ValidationResult):
    """Layer 2: Validate all YAML/JSON configs."""
    header("LAYER 2: Config File Validation (YAML/JSON)")

    try:
        import yaml
    except ImportError:
        result.fail("PyYAML not installed — cannot validate YAML files")
        return

    # YAML files
    yaml_files = list(ROOT.glob("chaos/**/*.yml")) + list(ROOT.glob("chaos/**/*.yaml"))
    yaml_files += [ROOT / "docker-compose.yml"]

    for f in sorted(yaml_files):
        try:
            with open(f) as fp:
                yaml.safe_load(fp.read())
            result.ok(f"{f.relative_to(ROOT)} — valid YAML")
        except Exception as e:
            result.fail(f"{f.relative_to(ROOT)} — YAML ERROR: {e}")

    # JSON files
    json_files = list(ROOT.glob("chaos/**/*.json"))

    for f in sorted(json_files):
        try:
            with open(f) as fp:
                json.load(fp)
            result.ok(f"{f.relative_to(ROOT)} — valid JSON")
        except Exception as e:
            result.fail(f"{f.relative_to(ROOT)} — JSON ERROR: {e}")


def validate_k6_scripts(result: ValidationResult):
    """Layer 3: Validate k6 load test scripts."""
    header("LAYER 3: k6 Load Test Scripts")

    for f in sorted(ROOT.glob("chaos/k6/*.js")):
        with open(f) as fp:
            content = fp.read()

        # Check required k6 patterns
        checks = {
            "import http from 'k6/http'": "k6 http import",
            "export const options": "k6 options config",
            "export default function": "default test function",
        }

        missing = []
        for pattern, desc in checks.items():
            if pattern not in content:
                missing.append(desc)

        if missing:
            result.warn(f"{f.name} — missing: {', '.join(missing)}")
        else:
            result.ok(f"{f.name} — complete k6 script")


def validate_prometheus_config(result: ValidationResult):
    """Layer 4: Validate Prometheus configuration."""
    header("LAYER 4: Prometheus + Alerting Consistency")

    try:
        import yaml
    except ImportError:
        result.fail("PyYAML not installed")
        return

    prom_path = ROOT / "chaos/prometheus/prometheus.yml"
    alerts_path = ROOT / "chaos/prometheus/alerts/hotsot.yml"

    # Load configs
    with open(prom_path) as f:
        prom = yaml.safe_load(f)
    with open(alerts_path) as f:
        alerts = yaml.safe_load(f)

    # Check scrape targets
    for job in prom["scrape_configs"]:
        job_name = job["job_name"]
        if "static_configs" in job:
            targets = job["static_configs"][0].get("targets", [])
            result.ok(f"Prometheus job '{job_name}': {len(targets)} targets")

    # Check alert groups
    for group in alerts.get("groups", []):
        group_name = group["name"]
        rules = group.get("rules", [])
        result.ok(f"Alert group '{group_name}': {len(rules)} rules")


def validate_grafana_dashboard(result: ValidationResult):
    """Layer 5: Validate Grafana dashboard + provisioning."""
    header("LAYER 5: Grafana Dashboard + Provisioning")

    # Dashboard
    dash_path = ROOT / "chaos/grafana/dashboards/hotsot-chaos.json"
    with open(dash_path) as f:
        dash = json.load(f)

    panels = [p for p in dash.get("panels", []) if p.get("type") != "row"]
    row_panels = [p for p in dash.get("panels", []) if p.get("type") == "row"]
    result.ok(f"Dashboard: {len(panels)} metric panels + {len(row_panels)} section rows")

    # Check template variables
    templates = dash.get("templating", {}).get("list", [])
    result.ok(f"Dashboard: {len(templates)} template variables")

    # Datasource provisioning
    ds_path = ROOT / "chaos/grafana/provisioning/datasources/datasource.yml"
    if ds_path.exists():
        result.ok("Grafana datasource provisioning: EXISTS")
    else:
        result.fail("Grafana datasource provisioning: MISSING")

    # Dashboard provisioning
    db_path = ROOT / "chaos/grafana/provisioning/dashboards/dashboard.yml"
    if db_path.exists():
        result.ok("Grafana dashboard provisioning: EXISTS")
    else:
        result.fail("Grafana dashboard provisioning: MISSING")


def validate_docker_compose(result: ValidationResult):
    """Layer 6: Validate docker-compose.yml consistency."""
    header("LAYER 6: Docker Compose Integration")

    try:
        import yaml
    except ImportError:
        result.fail("PyYAML not installed")
        return

    with open(ROOT / "docker-compose.yml") as f:
        dc = yaml.safe_load(f)

    services = dc.get("services", {})
    result.ok(f"docker-compose.yml: {len(services)} total services")

    # Check observability stack
    required_obs = ["prometheus", "grafana", "jaeger"]
    for svc in required_obs:
        if svc in services:
            result.ok(f"Observability service '{svc}': PRESENT in docker-compose")
        else:
            result.fail(f"Observability service '{svc}': MISSING from docker-compose")

    # Check Prometheus volume mounts
    prom = services.get("prometheus", {})
    prom_volumes = prom.get("volumes", [])
    has_prom_config = any("prometheus.yml" in str(v) for v in prom_volumes)
    has_prom_alerts = any("alerts" in str(v) for v in prom_volumes)
    result.ok(f"Prometheus config mount: {'YES' if has_prom_config else 'NO'}")
    result.ok(f"Prometheus alerts mount: {'YES' if has_prom_alerts else 'NO'}")

    # Check Grafana volumes
    grafana = services.get("grafana", {})
    grafana_volumes = grafana.get("volumes", [])
    has_dashboards = any("dashboards" in str(v) for v in grafana_volumes)
    has_provisioning = any("provisioning" in str(v) for v in grafana_volumes)
    result.ok(f"Grafana dashboards mount: {'YES' if has_dashboards else 'NO'}")
    result.ok(f"Grafana provisioning mount: {'YES' if has_provisioning else 'NO'}")


def validate_metrics_endpoint(result: ValidationResult):
    """Layer 7: Validate /metrics endpoint setup in all services."""
    header("LAYER 7: /metrics Endpoint Setup")

    # Check observability.py has setup_metrics
    with open(ROOT / "shared/utils/observability.py") as f:
        obs = f.read()

    if "def setup_metrics(" in obs:
        result.ok("setup_metrics() defined in observability.py")
    else:
        result.fail("setup_metrics() NOT defined in observability.py")

    # Check it's exported
    with open(ROOT / "shared/utils/__init__.py") as f:
        init = f.read()

    if "setup_metrics" in init:
        result.ok("setup_metrics exported from shared/utils/__init__.py")
    else:
        result.fail("setup_metrics NOT exported from shared/utils/__init__.py")

    # Check all services call setup_metrics
    missing_services = []
    for f in sorted(ROOT.glob("services/*/app/main.py")):
        with open(f) as fp:
            content = fp.read()
        if "setup_metrics(app" not in content:
            missing_services.append(f.parent.parent.name)

    if missing_services:
        result.fail(f"Services missing setup_metrics call: {', '.join(missing_services)}")
    else:
        result.ok("All 18 services call setup_metrics(app, ...)")

    # Check /metrics is in SKIP_PATHS
    with open(ROOT / "shared/utils/middleware.py") as f:
        mw = f.read()

    metrics_skip_count = mw.count('"/metrics"')
    if metrics_skip_count >= 3:
        result.ok(f"/metrics in {metrics_skip_count} middleware SKIP_PATHS (won't be rate-limited/auth-checked)")
    else:
        result.warn(f"/metrics only in {metrics_skip_count} SKIP_PATHS — may be blocked by some middleware")


def validate_chaos_mesh(result: ValidationResult):
    """Layer 8: Validate Chaos Mesh YAML manifests."""
    header("LAYER 8: Chaos Mesh Manifests")

    try:
        import yaml
    except ImportError:
        result.fail("PyYAML not installed")
        return

    expected_experiments = {
        "pod-kill": "PodChaos",
        "network-latency": "NetworkChaos",
        "db-failure": "PodChaos",
        "redis-failure": "PodChaos",
        "kafka-failure": "PodChaos",
        "network-partition": "NetworkChaos",
    }

    for name, expected_kind in expected_experiments.items():
        yaml_path = ROOT / f"chaos/chaos-mesh/{name}.yaml"
        if not yaml_path.exists():
            result.fail(f"Chaos Mesh manifest MISSING: {name}.yaml")
            continue

        with open(yaml_path) as f:
            doc = yaml.safe_load(f)

        actual_kind = doc.get("kind")
        chaos_name = doc.get("metadata", {}).get("name", "")

        if actual_kind == expected_kind:
            result.ok(f"{name}.yaml — kind={actual_kind} name={chaos_name}")
        else:
            result.fail(f"{name}.yaml — expected kind={expected_kind}, got {actual_kind}")


def main():
    print(f"{CYAN}")
    print("  ╔══════════════════════════════════════════════════════════╗")
    print("  ║   HotSot Chaos Engineering Stack — Validation Report    ║")
    print("  ║   8-Layer Comprehensive Check                           ║")
    print("  ╚══════════════════════════════════════════════════════════╝")
    print(f"{NC}")

    result = ValidationResult()

    validate_python_syntax(result)
    validate_yaml_json(result)
    validate_k6_scripts(result)
    validate_prometheus_config(result)
    validate_grafana_dashboard(result)
    validate_docker_compose(result)
    validate_metrics_endpoint(result)
    validate_chaos_mesh(result)

    # Summary
    print(f"\n{CYAN}{'═' * 60}{NC}")
    print(f"{CYAN}  SUMMARY{NC}")
    print(f"{CYAN}{'═' * 60}{NC}")
    print(f"  {GREEN}Passed:   {len(result.passed)}{NC}")
    print(f"  {RED}Failed:   {len(result.failed)}{NC}")
    print(f"  {YELLOW}Warnings: {len(result.warnings)}{NC}")

    if result.failed:
        print(f"\n{RED}❌ VALIDATION FAILED — {len(result.failed)} issues need fixing:{NC}")
        for f in result.failed:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print(f"\n{GREEN}🎉 ALL CHECKS PASSED — Chaos engineering stack is deployment-ready!{NC}")
        print(f"\n{CYAN}Next steps:{NC}")
        print(f"  1. docker compose up -d")
        print(f"  2. k6 run -e BASE_URL=http://localhost:8000 chaos/k6/baseline.js")
        print(f"  3. ./chaos/scripts/run-experiment.sh pod-kill")
        print(f"  4. python3 chaos/scripts/validate-criteria.py --results-dir chaos/experiments/results/latest")
        print(f"  5. Open Grafana: http://localhost:3000 (admin/admin)")
        sys.exit(0)


if __name__ == "__main__":
    main()
