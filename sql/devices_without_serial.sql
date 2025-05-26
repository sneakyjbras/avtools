-- sql/devices_without_serial.sql
SELECT ed.*
FROM eam_devices ed
WHERE NOT EXISTS (
  SELECT 1
  FROM landb_devices ld
  WHERE ld.equipmentno = ed.equipmentno
)
AND (ed.serialnumber IS NULL OR TRIM(ed.serialnumber) = '');
