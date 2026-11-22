{#
  Custom generic test: fails if any row has a value <= 0 in the given column.
  A dbt test "passes" when its query returns zero rows.
#}
{% test positive_value(model, column_name) %}
select *
from {{ model }}
where {{ column_name }} <= 0
{% endtest %}
