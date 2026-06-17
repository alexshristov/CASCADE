import ast
import sys
import os
import re
import random
import itertools
import statistics
import os
import matplotlib

if os.environ.get("DISPLAY", "") == "":
    matplotlib.use("Agg")  # headless-safe
import matplotlib.pyplot as plt


def compute_metrics(counts, mapping):
    always_positive = 0
    always_negative = 0

    self_consistent_correct = 0

    for fix, inco in mapping.items():
        if inco in counts["TP"] and fix in counts["TN"]:
            self_consistent_correct += 1

        if inco in counts["TP"] and fix in counts["FP"]:
            always_positive += 1
        if fix in counts["TN"] and inco in counts["FN"]:
            always_negative += 1

    TP = len(counts['TP'])
    FP = len(counts['FP'])
    FN = len(counts['FN'])
    TN = len(counts['TN'])
    prec = TP / (TP + FP) if (TP + FP) > 0 else 0
    rec = TP / (TP + FN) if (TP + FN) > 0 else 0
    spec = TN / (TN + FP) if (TN + FP) > 0 else 0
    acc = (TP + TN) / (TP + TN + FP + FN) if (TP + TN + FP + FN) > 0 else 0
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0
    documentation_invariant = (always_positive + always_negative) / len(mapping)
    PFP = self_consistent_correct / TP if TP > 0 else 0

    return {
        "TP": TP,
        "FP": FP,
        "TN": TN,
        "FN": FN,
        "Prec": f"{prec:.3f}",
        "Rec": f"{rec:.3f}",
        "F1": f"{f1:.3f}",
        "Spec": f"{spec:.3f}",
        "acc": f"{acc:.3f}",
        "doc_invariant": f"{documentation_invariant:.3f}",
        "always_pos": always_positive,
        "always_neg": always_negative,
        "self_cons_correct": self_consistent_correct,
        "PFP": f"{PFP:.3f}",

    }


def aggregate_metrics(per_run_results):
    """
    If runs == 1:
        floats -> "0.812"
        ints   -> "37"
    If runs > 1:
        floats -> "mean±std | median [min–max]"
        ints   -> "mean±std | median [min–max]"
    """
    float_fields = ["Prec", "Rec", "F1", "Spec", "acc",
                    "doc_invariant", "PFP"]

    int_fields = ["TP", "FP", "TN", "FN",
                  "always_pos", "always_neg", "self_cons_correct"]

    runs = len(per_run_results)
    out = {"runs": runs}

    # --- single-run: just print the value ---
    if runs == 1:
        r = per_run_results[0]
        for f in float_fields:
            out[f] = f"{float(r[f]):.3f}"
        for f in int_fields:
            out[f] = f"{int(r[f])}"
        return out

    # --- multi-run: mean±std | median [min–max] ---
    for f in float_fields:
        vals = [float(r[f]) for r in per_run_results]
        mean = statistics.mean(vals)
        std = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        med = statistics.median(vals)
        mn = min(vals)
        mx = max(vals)
        out[f] = f"{mean:.3f}±{std:.3f} | {med:.3f} [{mn:.3f}–{mx:.3f}]"

    for f in int_fields:
        vals = [int(r[f]) for r in per_run_results]
        mean = statistics.mean(vals)
        std = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        med = statistics.median(vals)
        mn = min(vals)
        mx = max(vals)
        out[f] = f"{mean:.2f}±{std:.2f} | {med:.0f} [{mn:d}–{mx:d}]"

    return out


def walk_directory(target, root='./java/', allowed_c_dirs=None):
    """
    allowed_c_dirs:
      - None        -> include all c-variants and non-c
      - empty set() -> include NO c-variants (only non-c)
      - set(keys)   -> include only those c-variant dirs (by relative path key)
    """
    target_file = 'result_' + target + ".txt"
    for dirpath, _, filenames in os.walk(root):
        if target_file not in filenames:
            continue

        path = os.path.join(dirpath, target_file)

        parent = os.path.basename(dirpath).lower()
        is_c = parent.startswith("c")

        if not is_c:
            yield path
            continue

        # c-variant
        key = os.path.relpath(dirpath, root).replace("\\", "/").lower()
        if allowed_c_dirs is None:
            yield path
        elif key in allowed_c_dirs:
            yield path
        else:
            continue


def get_groundtruth(file_path):
    id_path = os.path.dirname(file_path)
    ground_truth_file = os.path.join(id_path, "inconsistency.txt")
    with open(ground_truth_file, 'r') as f2:
        content2 = f2.read()
        gt = ast.literal_eval(content2)
    return gt


def get_prediction(file_path):
    with open(file_path, 'r') as f:
        prediction = f.read().strip()
    return prediction


def is_c_variant(result_path):
    """True iff the immediate parent directory is like c1, c2, etc.."""
    parent = os.path.basename(os.path.dirname(result_path))
    return parent.lower().startswith("c")


def list_available_c_dirs(driver, root='./java/'):
    """Return sorted list of UNIQUE c-variant directory keys that contain result_<driver>.txt."""
    target_file = 'result_' + driver + ".txt"
    c_keys = set()

    for dirpath, _, filenames in os.walk(root):
        if target_file in filenames:
            parent = os.path.basename(dirpath).lower()
            if parent.startswith("c"):
                key = os.path.relpath(dirpath, root).replace("\\", "/").lower()
                c_keys.add(key)
    return sorted(c_keys)


def c_dir_key_from_result_path(result_path, root='./java/'):
    """
    Returns a unique identifier for the c-directory that contains result_path,
    as a relative path from root, e.g. '1234/c7' or 'foo/bar/c12'.
    """
    c_dir = os.path.dirname(result_path)  # directory containing result_*.txt
    return os.path.relpath(c_dir, root).replace("\\", "/").lower()


def sample_c_dir_subsets(c_dirs, k, max_samples=1000, seed=0):
    """
    Sample up to `max_samples` UNIQUE subsets (as frozensets) of size k from c_dirs.
    If k == 0: returns [frozenset()].
    If k > len(c_dirs): uses k = len(c_dirs).
    """
    if k <= 0:
        return [frozenset()]
    k = min(k, len(c_dirs))

    # If total combos are small, just enumerate all and sample if needed
    total = 1
    # compute nCk safely-ish without huge ints? Python ints are fine; but we also can short-circuit
    n = len(c_dirs)
    # quick nCk
    from math import comb
    total = comb(n, k)

    rng = random.Random(seed)

    if total <= max_samples:
        return [frozenset(t) for t in itertools.combinations(c_dirs, k)]

    seen = set()
    subsets = []
    tries = 0
    # try hard to get unique samples without hanging if space is small
    while len(subsets) < max_samples and tries < max_samples * 50:
        s = frozenset(rng.sample(c_dirs, k))
        if s not in seen:
            seen.add(s)
            subsets.append(s)
        tries += 1

    return subsets


# ---------------- plotting
def aggregate_metrics_numeric(per_run_results):
    """
    Returns numeric summaries for each metric:
      metric -> {"runs": n, "mean":..., "std":..., "median":..., "min":..., "max":...}
    Values are floats for float-metrics and ints for count-metrics.
    """
    float_fields = ["Prec", "Rec", "F1", "Spec", "acc",
                    "doc_invariant", "PFP"]
    int_fields = ["TP", "FP", "TN", "FN",
                  "always_pos", "always_neg", "self_cons_correct"]

    runs = len(per_run_results)
    out = {"runs": runs}

    def summarize(vals):
        if not vals:
            return {"mean": 0.0, "std": 0.0, "median": 0.0, "min": 0.0, "max": 0.0}
        mean = statistics.mean(vals)
        std = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        med = statistics.median(vals)
        return {"mean": mean, "std": std, "median": med, "min": min(vals), "max": max(vals)}

    for f in float_fields:
        vals = [float(r[f]) for r in per_run_results]
        out[f] = summarize(vals)

    for f in int_fields:
        vals = [int(r[f]) for r in per_run_results]
        out[f] = summarize(vals)

    return out

def plot_compare_drivers(
        driver_abbrev_map,
        series_specs,
        c_label_pairs,
        metric,
        stat,
        mapping,
        seed=0,
        root="./java/",
        plot_out="plot.pdf",
        plot_noshow=False
):
    """
    series_specs: list of dicts:
      {"driver_code": "c", "driver_name": "CASCADE",
       "version_key": "phase2", "display": "CASCADE P2"}
    c_label_pairs: list of (c_value:int, label:str)
    """

    x_labels = [lab for _, lab in c_label_pairs]
    xs = list(range(len(c_label_pairs)))  # categorical x

    # build y-series per line
    series_to_ys = {s["display"]: {"center": [], "min": [], "max": []} for s in series_specs}

    # pre-discover c-dir keys per driver (so sampling is driver-specific)
    c_dirs_by_driver = {}
    for s in series_specs:
        drv = s["driver_name"]
        if drv not in c_dirs_by_driver:
            c_dirs_by_driver[drv] = list_available_c_dirs(drv, root=root)

    # compute points
    for c_val, _lab in c_label_pairs:
        for s in series_specs:
            drv = s["driver_name"]
            version_key = s["version_key"]
            display = s["display"]

            c_dirs = c_dirs_by_driver[drv]
            print("calculating different combinations")
            subsets = sample_c_dir_subsets(c_dirs, c_val, max_samples=1000, seed=seed)  # change smapling size here

            # collect metrics across sampled runs for THIS driver/version
            run_metrics = []
            for chosen in subsets:
                versions = evaluate_driver(drv, allowed_c_dirs=set(chosen))
                if not versions or version_key not in versions:
                    continue
                metrics = compute_metrics(versions[version_key], mapping)
                run_metrics.append(metrics)

            if not run_metrics:
                series_to_ys[display]["center"].append(float("nan"))
                series_to_ys[display]["min"].append(float("nan"))
                series_to_ys[display]["max"].append(float("nan"))
                continue

            num = aggregate_metrics_numeric(run_metrics)
            if metric not in num:
                raise ValueError(f"Unknown metric {metric!r} for plotting")

            summary = num[metric]

            center_val = summary[stat]
            min_val = summary["min"]
            max_val = summary["max"]

            series_to_ys[display]["center"].append(center_val)
            series_to_ys[display]["min"].append(min_val)
            series_to_ys[display]["max"].append(max_val)

    plt.figure(figsize=(7, 4), dpi=200)
    ax = plt.gca()

    colors = [
        "#d17c00",  # orange
        "#2e6f57",  # green
        "#9e2a2b",  # red
        "#1f4e79"  # blue
    ]
    markers = ["d", "s", "^", "o"]

    for i, display in enumerate(list(series_to_ys.keys())[::-1]):
        centers = series_to_ys[display]["center"]
        mins = series_to_ys[display]["min"]
        maxs = series_to_ys[display]["max"]

        color = colors[i % len(colors)]
        marker = markers[i % len(markers)]

        ax.fill_between(xs, mins, maxs, color=color, alpha=0.15, linewidth=0, zorder=1)
        ax.plot(
            xs, centers,
            color=color,
            marker=marker,
            markersize=6,
            linewidth=2,
            # markeredgewidth=0.6,
            # markeredgecolor="white",
            label=display,
            zorder=2
        )

    ax.set_xticks(xs)
    ax.set_xticklabels(x_labels)  # , rotation=30, ha="right")
    ax.set_ylabel(f"{metric} ({stat})")
    ax.set_xlabel("Percentage of consistent samples (total amount)")
    ax.grid(True, which="major", axis="y", linestyle="-", linewidth=0.6, alpha=0.3)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    ax.legend(frameon=False, loc="upper right", bbox_to_anchor=(0.98, 0.98))
    plt.tight_layout()
    plt.savefig(plot_out, bbox_inches="tight")

    if not plot_noshow:
        plt.show()
    plt.close()


"""
    # plot
    plt.figure()

    for display in series_to_ys.keys():
        centers = series_to_ys[display]["center"]
        mins = series_to_ys[display]["min"]
        maxs = series_to_ys[display]["max"]

        line, = plt.plot(xs, centers, marker="o", label=display)
        plt.fill_between(xs, mins, maxs, alpha=0.2)

    plt.xticks(xs, x_labels, rotation=30, ha="right")
    plt.xlabel("")
    plt.ylabel(f"{metric} ({stat})")
    #plt.title("Driver comparison over c")
    plt.legend()
    plt.tight_layout()
    plt.savefig(plot_out)
    print(f"[plot] saved to {plot_out}")
    if not plot_noshow:
        plt.show()
    plt.close()
"""


def eval_cascade(gt, pd):
    """Returns a dict with the specific results (TP TN etc.) for each version  (phase 1  and phase 2)"""
    # results look like this:

    results = {
    }

    # here just getting into phase 2 already means we have predicted a positive
    prediction = "Positive" if "step 2" in pd else "Negative"
    if prediction == "Positive":
        results["phase1"] = "TP" if gt else "FP"
    else:
        results["phase1"] = "FN" if gt else "TN"

    if "INCO" in pd:
        results["phase2"] = "TP" if gt else "FP"
    else:
        results["phase2"] = "FN" if gt else "TN"

    # parse the result string which looks like this: NoInco; fail; step 2 (C'+T'); (9, 1, 0); (9, 1, 0); p2p: 9, f2f: 1, p2f: 0, f2p: 0; og tests exist; 1
    parts = [p.strip() for p in pd.split(";")]

    num_repair_steps = parts[-1]

    segment = parts[5] if len(parts) > 5 else ""
    if not segment:
        info = {"p2p": 0, "f2f": 0, "p2f": 0, "f2p": 0}
    else:
        # turn  p2p: 9, f2f: 1, …   into   "p2p": 9, "f2f": 1, …
        quoted = re.sub(r"(\w+)\s*:", r'"\1":', segment)
        info = ast.literal_eval("{" + quoted + "}")

    if info["f2p"] > 0:
        results["f2p>0"] = "TP" if gt else "FP"
    else:
        results["f2p>0"] = "FN" if gt else "TN"

    if info["f2p"] > info["p2f"]:
        results["f2p>p2f"] = "TP" if gt else "FP"
    else:
        results["f2p>p2f"] = "FN" if gt else "TN"

    if info["f2p"] > 0 and info["p2f"] == 0:
        results["f2p>0,p2f=0"] = "TP" if gt else "FP"
    else:
        results["f2p>0,p2f=0"] = "FN" if gt else "TN"
    results["nop2f"] = results["f2p>0,p2f=0"]

    return results


def eval_baseline(gt, pd):
    # results look like this:     e.g. Negative; Negative; Positive; Negative;    labels simple-Consistent; simple-Inconsistent; complex-Consistent; complex-Inconsistent
    split_predictions = pd.replace(" ", "").split(";")
    labels = ["simpleConsistent", "simpleInconsistent", "complexConsistent", "complexInconsistent"]
    results = {}

    for i in range(4):
        if split_predictions[i] == "Positive":
            results[labels[i]] = "TP" if gt else "FP"
        else:
            results[labels[i]] = "FN" if gt else "TN"

    more_labels = ["atLeast1", "atLeast2", "atLeast3", "all4"]
    num_of_positives = split_predictions.count("Positive")

    for i in range(4):
        if num_of_positives >= i + 1:
            results[more_labels[i]] = "TP" if gt else "FP"
        else:
            results[more_labels[i]] = "FN" if gt else "TN"

    return results


def eval_docchecker(gt, pd):
    # results look like this:     Error; Negative; Positive; [Negative,Positive,Negative]       asCodeis, oneLine, singleLines, whole results
    split_predictions = pd.replace(" ", "").split(";")
    labels = ["asCodeis", "oneLine", "singleLines"]
    results = {}

    for i in range(3):
        if split_predictions[i] == "Positive":
            results[labels[i]] = "TP" if gt else "FP"
        else:
            results[labels[i]] = "FN" if gt else "TN"

    return results


def eval_jdoctor(gt, pd):
    pass


def evaluate_driver(driver, allowed_c_dirs=set()):
    """Returns dict counts[version] with TP/FP/FN/TN lists."""
    if driver == "CASCADE":
        eval = eval_cascade
    elif driver == "Baseline":
        eval = eval_baseline
    elif driver == "DocChecker":
        eval = eval_docchecker
    elif driver == "C4RLLaMA":
        eval = eval_docchecker
    else:
        return

    counts = {}
    for result_path in walk_directory(driver, allowed_c_dirs=allowed_c_dirs):
        id = os.path.dirname(result_path).replace("./java/", "")

        gt = get_groundtruth(result_path)
        pd = get_prediction(result_path)
        eval_results = eval(gt, pd)

        for version, result in eval_results.items():
            if version not in counts:
                counts[version] = {"TP": [], "FP": [], "FN": [], "TN": []}
            counts[version][result].append(id)

    return counts


def print_report(driver_name, results, to_file=False):
    print(driver_name)
    for phase, metrics in results.items():
        print(f"   {phase}: {str(metrics)}")
    print()


if __name__ == '__main__':
    from dataset_mapping_dict import mapping

    driver_abbreviations = {
        "c": "CASCADE",
        "b": "Baseline",
        "d": "DocChecker",
        "c4": "C4RLLaMA"
    }

    if len(sys.argv) < 2:
        sys.exit("Usage: python eval.py <letter> [<letter> ...] [--c N] [--seed S]")

    # defaults
    c_num = 0
    seed = 0
    selected_metrics = None
    plot_mode = False
    plot_points = []  # list of (c, label)
    plot_series_specs = []
    plot_metric = "F1"
    plot_stat = "median"
    plot_out = "plot.png"
    plot_noshow = False

    drivers = []
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--plot":
            plot_mode = True
            i += 1
            continue

        if arg == "--plot-metric":
            plot_metric = sys.argv[i + 1]
            i += 2
            continue

        if arg == "--plot-stat":
            plot_stat = sys.argv[i + 1].lower()
            if plot_stat not in ("mean", "median"):
                sys.exit("Error: --plot-stat must be mean or median")
            i += 2
            continue

        if arg == "--plot-points":
            i += 1
            while i < len(sys.argv) and not sys.argv[i].startswith("--"):
                token = sys.argv[i]
                if "=" not in token:
                    sys.exit(f"Error: plot point must be like 5=five, got {token!r}")
                left, right = token.split("=", 1)
                plot_points.append((int(left), right))
                i += 1
            continue

        if arg == "--plot-series":
            i += 1
            while i < len(sys.argv) and not sys.argv[i].startswith("--"):
                token = sys.argv[i]

                # token: driverCode:versionKey=DisplayName   (DisplayName optional)
                display = None
                if "=" in token:
                    token, display = token.split("=", 1)
                    display = display.strip('"')

                if ":" not in token:
                    sys.exit(f"Error: plot series must be like c:phase2=Name, got {token!r}")
                dcode, vkey = token.split(":", 1)

                # map driver code -> driver name
                try:
                    dname = driver_abbreviations[dcode.lower()]
                except KeyError:
                    sys.exit(f"Error: unknown driver code in plot series: {dcode!r}")

                if display is None:
                    display = f"{dname}:{vkey}"

                plot_series_specs.append({
                    "driver_code": dcode.lower(),
                    "driver_name": dname,
                    "version_key": vkey,
                    "display": display
                })
                i += 1
            continue

        if arg == "--plot-out":
            if i + 1 >= len(sys.argv):
                sys.exit("Error: --plot-out expects a filename, e.g. --plot-out fig.png")
            plot_out = sys.argv[i + 1]
            i += 2
            continue

        if arg == "--plot-noshow":
            plot_noshow = True
            i += 1
            continue

        if arg == "--c":
            if i + 1 >= len(sys.argv):
                sys.exit("Error: --c expects an integer")
            c_num = int(sys.argv[i + 1])
            i += 2
            continue

        if arg == "--seed":
            if i + 1 >= len(sys.argv):
                sys.exit("Error: --seed expects an integer")
            seed = int(sys.argv[i + 1])
            i += 2
            continue

        if arg == "--metrics":
            selected_metrics = []
            i += 1
            while i < len(sys.argv) and not sys.argv[i].startswith("--"):
                selected_metrics.append(sys.argv[i])
                i += 1
            continue

        try:
            drivers.append(driver_abbreviations[arg.lower()])
        except KeyError:
            sys.exit(f"Unknown letter code: {arg!r}")
        i += 1

    if plot_mode:
        print("[plot] entering plot mode")
        print("[plot] points:", plot_points)
        print("[plot] series:", plot_series_specs)
        print("[plot] out:", plot_out)

        if not plot_points:
            sys.exit("Plot mode needs --plot-points like: --plot-points 0=core 5=five 10=ten")
        if not plot_series_specs:
            sys.exit(
                "Plot mode needs --plot-series like: --plot-series c:phase2=\"CASCADE P2\" b:atLeast1=\"Baseline ≥1\"")

        plot_compare_drivers(
            driver_abbrev_map=driver_abbreviations,
            series_specs=plot_series_specs,
            c_label_pairs=plot_points,
            metric=plot_metric,
            stat=plot_stat,
            mapping=mapping,
            seed=seed,
            root="./java/",
            plot_out=plot_out,
            plot_noshow=plot_noshow
        )
        sys.exit(0)

    for driver in drivers:
        # discover c dirs for this driver
        c_dirs = list_available_c_dirs(driver)
        subsets = sample_c_dir_subsets(c_dirs, c_num, max_samples=1000, seed=seed)

        print(
            f"{driver} | available c-dirs: {len(c_dirs)} | using c={min(max(c_num, 0), len(c_dirs))} | sampled runs: {len(subsets)}")

        # Collect metrics per version across runs
        per_version_runs = {}  # version -> list[metrics]

        for chosen in subsets:
            versions = evaluate_driver(driver, allowed_c_dirs=set(chosen))

            for version, counts in versions.items():
                metrics = compute_metrics(counts, mapping)
                per_version_runs.setdefault(version, []).append(metrics)

        # Aggregate and print
        print(driver)
        for version, run_metrics in per_version_runs.items():
            agg = aggregate_metrics(run_metrics)

            # Decide which metrics to print
            if selected_metrics is None:
                metrics_to_print = {k: v for k, v in agg.items() if k != "runs"}
            else:
                metrics_to_print = {k: agg[k] for k in selected_metrics if k in agg}

            print(f"   {version}: {metrics_to_print}")

        print()






