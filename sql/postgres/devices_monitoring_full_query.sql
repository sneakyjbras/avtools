-- sql/devices_monitoring_full_query.sql

-- ------------------------------------------------------
-- 1) Helpful indexes for performance:
--    CREATE INDEX idx_positions_equipmentno   ON eam_positions(equipmentno);
--    CREATE INDEX idx_positions_parentasset   ON eam_positions(parentasset);
--    CREATE INDEX idx_devices_position        ON eam_devices(position);
-- ------------------------------------------------------

WITH RECURSIVE device_hierarchy AS (
  -- Anchor: each device’s direct position
  SELECT
    d.equipmentno      AS DeviceNo,
    d.position         AS PositionNo,
    p.parentasset      AS ParentPosition,
    1                  AS Depth
  FROM eam_devices d
  LEFT JOIN eam_positions p
    ON d.position = p.equipmentno

  UNION ALL

  -- Recursive step: climb from ParentPosition → equipmentno
  SELECT
    dh.DeviceNo,
    p.equipmentno      AS PositionNo,
    p.parentasset      AS ParentPosition,
    dh.Depth + 1       AS Depth
  FROM device_hierarchy dh
  JOIN eam_positions p
    ON dh.ParentPosition = p.equipmentno
),

ranked_hierarchy AS (
  -- For each device, pick the row with greatest depth
  SELECT
    DeviceNo,
    PositionNo        AS ConferenceRoom,
    ROW_NUMBER() OVER (
      PARTITION BY DeviceNo
      ORDER BY Depth DESC
    )                  AS Rn
  FROM device_hierarchy
)

SELECT
  COALESCE(ed.equipmentdesc, ld.equipmentdesc)   AS Name,
  COALESCE(ld.manufacturer, ed.manufacturer)     AS Manufacturer,
  pos.equipmentdesc                              AS ConferenceRoom,
  ed.commissiondate                              AS CommissionDate,
  ed.equipmentno                                 AS EquipmentNo,
  CASE WHEN ld.equipmentno IS NOT NULL THEN 1 END AS Resources
FROM eam_devices ed
LEFT JOIN landb_devices ld
  ON ed.equipmentno  = ld.equipmentno
 AND ed.serialnumber = ld.serialnumber

LEFT JOIN ranked_hierarchy rh
  ON ed.equipmentno = rh.DeviceNo
 AND rh.Rn         = 1

LEFT JOIN eam_positions pos
  ON pos.equipmentno = rh.ConferenceRoom

WHERE
  ed.eqclass   = 'AVD'
  AND ed.category = 'AV-PRO'
ORDER BY Name;
