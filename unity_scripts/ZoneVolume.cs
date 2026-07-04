// Phase 7: a named trigger-box zone. Add to an empty GameObject; the BoxCollider is
// added automatically and set to isTrigger. Size/position the collider over the area.
using UnityEngine;

public enum ZoneType { EntryGate, Barracks, Perimeter, Unknown }

[RequireComponent(typeof(BoxCollider))]
public class ZoneVolume : MonoBehaviour
{
    public ZoneType zoneType = ZoneType.Perimeter;
    public string zoneId = "Z0";
    public int priority = 0;   // when zones overlap, the higher priority wins in the log

    void Reset() => GetComponent<BoxCollider>().isTrigger = true;
}
