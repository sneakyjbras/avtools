-- sql/devices_monitoring_full_query_optimized

-- ------------------------------------------------------
-- Helpful indexes (as before):
--   CREATE INDEX idx_positions_equipmentno   ON eam_positions(equipmentno);
--   CREATE INDEX idx_positions_parentasset   ON eam_positions(parentasset);
--   CREATE INDEX idx_devices_position        ON eam_devices(position);
-- ------------------------------------------------------

WITH FilteredDevices AS (
  SELECT
    d.equipmentno      AS DeviceNo,
    d.position         AS PositionNo,
    d.equipmentdesc    AS Desc,
    d.manufacturer     AS Manufacturer,
    d.commissiondate   AS CommissionDate,
    d.serialnumber
  FROM eam_devices d
  WHERE d.eqclass   = 'AVD'
    AND d.category  = 'AV-PRO'
)

SELECT
  COALESCE(fd.Desc, ld.equipmentdesc)        AS Name,
  COALESCE(ld.manufacturer, fd.Manufacturer) AS Manufacturer,
  room.equipmentdesc                         AS ConferenceRoom,
  fd.CommissionDate                          AS CommissionDate,
  fd.DeviceNo                                AS EquipmentNo,
  CASE WHEN ld.equipmentno IS NOT NULL THEN 1 END AS Resources
FROM FilteredDevices fd

-- pull canonical data if it exists
LEFT JOIN landb_devices ld
  ON fd.DeviceNo     = ld.equipmentno
 AND fd.serialnumber = ld.serialnumber

-- find the deepest position for each device via a fast lateral recursive CTE
LEFT JOIN LATERAL (
  WITH RECURSIVE dh AS (
    -- start at the device's direct position
    SELECT
      p.equipmentno      AS PositionNo,
      p.parentasset      AS ParentPosition,
      1                  AS Depth
    FROM eam_positions p
    WHERE p.equipmentno = fd.PositionNo

    UNION ALL

    -- climb up until there's no more parent
    SELECT
      p.equipmentno,
      p.parentasset,
      dh.Depth + 1
    FROM dh
    JOIN eam_positions p
      ON dh.ParentPosition = p.equipmentno
    WHERE dh.Depth < 100  -- safety cap against cycles
  )
  -- take the single row with max Depth
  SELECT dh.PositionNo
  FROM dh
  ORDER BY dh.Depth DESC
  LIMIT 1
) AS deepest(PositionNo) ON TRUE

-- finally fetch that position's description
LEFT JOIN eam_positions room
  ON room.equipmentno = deepest.PositionNo

ORDER BY Name;
