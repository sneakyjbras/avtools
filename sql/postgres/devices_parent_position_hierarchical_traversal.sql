-- sql/devices_parent_position_hierarchical_traversal.sql
WITH RECURSIVE device_hierarchy AS (
  -- Anchor: each device’s direct position
  SELECT
    d.equipmentno      AS device_no,
    d.position         AS position_no,
    p.parentasset      AS parent_position,
    1                   AS depth
  FROM eam_devices d
  LEFT JOIN eam_positions p
    ON d.position = p.equipmentno

  UNION ALL

  -- Recursive step: climb from parent_position → equipmentno
  SELECT
    dh.device_no,
    p.equipmentno      AS position_no,
    p.parentasset      AS parent_position,
    dh.depth + 1       AS depth
  FROM device_hierarchy dh
  JOIN eam_positions p
    ON dh.parent_position = p.equipmentno
)
SELECT
  device_no,
  position_no,
  depth
FROM device_hierarchy
ORDER BY device_no, depth;
