SP=/private/tmp/claude-501/-Users-brandon-yjgit-brandon-semantic/b59c290c-8981-4e31-b72b-52d7f0c96767/scratchpad/mfsrc/lib/python3.11/site-packages
echo "=== AggregationType ==="; cat $SP/metricflow_semantic_interfaces/type_enums/aggregation_type.py | grep -vE "^\s*#|^$" | head -40
echo; echo "=== MetricType ==="; grep -E "= \"" $SP/metricflow_semantic_interfaces/type_enums/metric_type.py
echo; echo "=== TimeGranularity ==="; grep -E "= \"" $SP/metricflow_semantic_interfaces/type_enums/time_granularity.py
