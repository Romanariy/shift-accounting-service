"""Development fixture scoring. This output intentionally cannot enable auto-publication."""
import argparse
import json
import math
from pathlib import Path
import statistics
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from apps.offers.recognizer import merge_results, ENGINE_VERSION


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("report")
    parser.add_argument("--labels",default=str(Path(__file__).with_name("ds-reference.json")))
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    report=json.loads(Path(args.report).read_text(encoding="utf-8"))
    labels=json.loads(Path(args.labels).read_text(encoding="utf-8"))
    images={i["index"]:i for i in report["images"]}
    rows=[]
    for case in labels["cases"]:
        image=images[case["index"]]
        merged=merge_results([image],labels["profiles"])
        found=sorted([[i.get("start"),i.get("end")] for i in merged["intervals"]],key=str)
        expected=sorted(case["intervals"],key=str)
        complete=bool(case["date"]) and not case.get("requires_clarification") and all(a and b for a,b in expected)
        correct=complete and merged["date"]==case["date"] and merged["organization"]==case["organization"] and found==expected
        accepted=not merged["questions"]
        rows.append({"index":case["index"],"sha256":image["sha256"],"accepted_without_questions":accepted,
            "exact_fields_correct":correct,"reference_complete":complete,"questions":[q["key"] for q in merged["questions"]],
            "date":merged["date"],"intervals":found,"seconds":image["seconds"]})
    accepted=sum(r["accepted_without_questions"] for r in rows)
    correct=sum(r["accepted_without_questions"] and r["exact_fields_correct"] for r in rows)
    p=correct/accepted if accepted else 0
    z=1.959963984540054
    lower=(p+z*z/(2*accepted)-z*math.sqrt(p*(1-p)/accepted+z*z/(4*accepted*accepted)))/(1+z*z/accepted) if accepted else 0
    times=sorted(r["seconds"] for r in rows)
    pairs=sorted(rows[i]["seconds"]+rows[i+1]["seconds"] for i in range(len(rows)-1))
    output={"engine_version":ENGINE_VERSION,"independent":False,"split_by_album":False,
        "unit":"single_screenshot_development_fixture","total":len(rows),"accepted":accepted,"correct_accepted":correct,
        "precision":p,"lower_precision_95":lower,"coverage":accepted/len(rows),
        "latency":{"median_image_seconds":statistics.median(times),"p95_image_seconds":times[math.ceil(len(times)*.95)-1],
            "max_image_seconds":max(times),"median_adjacent_pair_seconds":statistics.median(pairs),
            "p95_adjacent_pair_seconds":pairs[math.ceil(len(pairs)*.95)-1],"max_adjacent_pair_seconds":max(pairs),
            "note":"Pairs are sums of consecutive warm inference durations, not measured Telegram end-to-end latency."},
        **{key:report.get(key) for key in ("platform","cpu","threads","offline","startup_seconds","rss_mb","cpu_seconds","two_image_probe","evaluation_today")},
        "limitations":["Used during development; correlated and repeated screenshots.","Organization IDs 2/3 are fixture hall families.","No independent album-level evaluation or target Linux CPU measurement."],
        "cases":rows}
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in output.items() if k!="cases"},ensure_ascii=True,indent=2))


if __name__=="__main__":
    main()
