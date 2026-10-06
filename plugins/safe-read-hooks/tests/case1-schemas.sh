SP=/private/tmp/claude-501/-Users-brandon-yjgit-brandon-semantic/b59c290c-8981-4e31-b72b-52d7f0c96767/scratchpad/mfsrc/lib/python3.11/site-packages
ls $SP/metricflow_semantic_interfaces/parsing/generated_json_schemas/ 2>/dev/null
echo "=== explicit schema keys ==="
python3 - <<'EOF'
import json,glob,os
p="/private/tmp/claude-501/-Users-brandon-yjgit-brandon-semantic/b59c290c-8981-4e31-b72b-52d7f0c96767/scratchpad/mfsrc/lib/python3.11/site-packages/metricflow_semantic_interfaces/parsing/generated_json_schemas"
for f in sorted(glob.glob(p+"/*.json")):
    d=json.load(open(f))
    print("──",os.path.basename(f))
    for k,v in d.items():
        if isinstance(v,dict) and "properties" in v:
            print("  ",k,"→",list(v["properties"].keys()))
EOF
