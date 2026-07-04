// Phase 7: goes on each agent prefab (root object, which must have a KINEMATIC Rigidbody -
// triggers never fire without one). Zone membership is LATCHED with Enter/Exit; a
// Stay-then-clear-per-frame pattern races the physics step and silently drops rows.
using System.Collections.Generic;
using UnityEngine;

public class AgentZoneReporter : MonoBehaviour
{
    public string objectType = "person";   // set to "vehicle" on the Vehicle prefab
    public float logInterval = 0.5f;       // sim-seconds between CSV rows (bounds file size)

    readonly HashSet<ZoneVolume> inside = new();
    float nextLog;

    void OnTriggerEnter(Collider o) { var z = o.GetComponent<ZoneVolume>(); if (z) inside.Add(z); }
    void OnTriggerExit(Collider o)  { var z = o.GetComponent<ZoneVolume>(); if (z) inside.Remove(z); }

    void Update()
    {
        // playback looped back to t=0 -> reset the schedule too
        if (nextLog > AgentPlayback.SimTime + logInterval) nextLog = 0f;
        if (inside.Count == 0 || AgentPlayback.SimTime < nextLog) return;
        nextLog = AgentPlayback.SimTime + logInterval;

        ZoneVolume best = null;
        foreach (var z in inside)
            if (best == null || z.priority >= best.priority) best = z;
        DetectionLogger.I?.Log(AgentPlayback.SimTime, objectType, best.zoneType, best.zoneId,
                               transform.position);
    }
}
