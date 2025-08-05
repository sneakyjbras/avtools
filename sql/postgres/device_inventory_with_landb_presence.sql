-- sql/device_inventory_with_landb_presence.sql
SELECT
  e.position AS "Room",
  COALESCE(e.eqclass,     a.eqclass) AS "Class",
  COALESCE(e.equipmentdesc, a.name) AS "Name",
  e.serialnumber AS "Serial No.",
  e.equipmentno AS "Equipment Id.",
  e.commissiondate AS "Commission Date",
  CASE WHEN a.equipmentno IS NOT NULL THEN 1 ELSE 0 END AS "LanDB Link"
FROM eam_devices e
LEFT JOIN landb_devices a
  ON e.equipmentno  = a.equipmentno
 AND e.serialnumber = a.serial_number;
