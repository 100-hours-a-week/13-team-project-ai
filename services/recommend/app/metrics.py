from prometheus_client import Counter

empty_recommend_counter = Counter(
    'recommend_empty_result_total',
    'Total number of empty recommendation results'
)