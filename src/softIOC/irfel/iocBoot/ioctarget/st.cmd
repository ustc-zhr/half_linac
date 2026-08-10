#!../../bin/linux-x86_64/target

#- You may have to change target to something else
#- everywhere it appears in this file

< envPaths

cd "${TOP}"

## Register all support components
dbLoadDatabase "dbd/target.dbd"
target_registerRecordDeviceDriver pdbbase

## Load record instances
dbLoadTemplate "db/irfel.substitutions"
#dbLoadRecords "db/targetVersion.db", "user=biaobin"
#dbLoadRecords "db/dbSubExample.db", "user=biaobin"

#- Set this to see messages from mySub
#-var mySubDebug 1

#- Run this to trace the stages of iocInit
#-traceIocInit

cd "${TOP}/iocBoot/${IOC}"
iocInit

## Start any sequence programs
#seq sncExample, "user=biaobin"
