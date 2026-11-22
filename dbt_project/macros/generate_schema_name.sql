{#
  By default dbt names custom schemas "<target_schema>_<custom>" (e.g. main_analytics).
  This override uses the custom name as-is, so models land in "staging" and "analytics".
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
