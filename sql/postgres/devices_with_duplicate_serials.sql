-- sql/devices_with_duplicate_serials.sql
SELECT
  ed.equipmentno,
  ed.serialnumber,
  ed.position,
  ed.equipmentdesc
FROM eam_devices ed
JOIN (
  SELECT serialnumber
  FROM eam_devices
  WHERE serialnumber IS NOT NULL
    AND TRIM(serialnumber) <> ''
  GROUP BY serialnumber
  HAVING COUNT(*) > 1
) dup
  ON ed.serialnumber = dup.serialnumber
ORDER BY ed.serialnumber, ed.equipmentno;
