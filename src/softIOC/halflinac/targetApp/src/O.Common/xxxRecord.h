/** @file xxxRecord.h
 * @brief Declarations for the @ref xxxRecord "xxx" record type.
 *
 * This header was generated from xxxRecord.dbd
 */

#ifndef INC_xxxRecord_H
#define INC_xxxRecord_H

#include "epicsTypes.h"
#include "link.h"
#include "epicsMutex.h"
#include "ellLib.h"
#include "devSup.h"
#include "epicsTime.h"

/** @brief Declaration of xxx record type. */
typedef struct xxxRecord {
    char                name[61];   /**< @brief Record Name */
    char                desc[41];   /**< @brief Descriptor */
    char                asg[29];    /**< @brief Access Security Group */
    epicsEnum16         scan;       /**< @brief Scan Mechanism */
    epicsEnum16         pini;       /**< @brief Process at iocInit */
    epicsInt16          phas;       /**< @brief Scan Phase */
    char                evnt[40];   /**< @brief Event Name */
    epicsInt16          tse;        /**< @brief Time Stamp Event */
    DBLINK              tsel;       /**< @brief Time Stamp Link */
    epicsEnum16         dtyp;       /**< @brief Device Type */
    epicsInt16          disv;       /**< @brief Disable Value */
    epicsInt16          disa;       /**< @brief Disable */
    DBLINK              sdis;       /**< @brief Scanning Disable */
    epicsMutexId        mlok;       /**< @brief Monitor lock */
    ELLLIST             mlis;       /**< @brief Monitor List */
    ELLLIST             bklnk;      /**< @brief Backwards link tracking */
    epicsUInt8          disp;       /**< @brief Disable putField */
    epicsUInt8          proc;       /**< @brief Force Processing */
    epicsEnum16         stat;       /**< @brief Alarm Status */
    epicsEnum16         sevr;       /**< @brief Alarm Severity */
    char                amsg[40];   /**< @brief Alarm Message */
    epicsEnum16         nsta;       /**< @brief New Alarm Status */
    epicsEnum16         nsev;       /**< @brief New Alarm Severity */
    char                namsg[40];  /**< @brief New Alarm Message */
    epicsEnum16         acks;       /**< @brief Alarm Ack Severity */
    epicsEnum16         ackt;       /**< @brief Alarm Ack Transient */
    epicsEnum16         diss;       /**< @brief Disable Alarm Sevrty */
    epicsUInt8          lcnt;       /**< @brief Lock Count */
    epicsUInt8          pact;       /**< @brief Record active */
    epicsUInt8          putf;       /**< @brief dbPutField process */
    epicsUInt8          rpro;       /**< @brief Reprocess  */
    struct asgMember    *asp;       /**< @brief Access Security Pvt */
    struct processNotify *ppn;      /**< @brief pprocessNotify */
    struct processNotifyRecord *ppnr; /**< @brief pprocessNotifyRecord */
    struct scan_element *spvt;      /**< @brief Scan Private */
    struct typed_rset   *rset;      /**< @brief Address of RSET */
    unambiguous_dset    *dset;      /**< @brief DSET address */
    void                *dpvt;      /**< @brief Device Private */
    struct dbRecordType *rdes;      /**< @brief Address of dbRecordType */
    struct lockRecord   *lset;      /**< @brief Lock Set */
    epicsEnum16         prio;       /**< @brief Scheduling Priority */
    epicsUInt8          tpro;       /**< @brief Trace Processing */
    epicsUInt8          bkpt;       /**< @brief Break Point */
    epicsUInt8          udf;        /**< @brief Undefined */
    epicsEnum16         udfs;       /**< @brief Undefined Alarm Sevrty */
    epicsTimeStamp      time;       /**< @brief Time */
    epicsUInt64         utag;       /**< @brief Time Tag */
    DBLINK              flnk;       /**< @brief Forward Process Link */
    epicsFloat64        val;        /**< @brief Current EGU Value */
    DBLINK              inp;        /**< @brief Input Specification */
    epicsInt16          prec;       /**< @brief Display Precision */
    char                egu[16];    /**< @brief Engineering Units */
    epicsFloat32        hopr;       /**< @brief High Operating Range */
    epicsFloat32        lopr;       /**< @brief Low Operating Range */
    epicsFloat32        hihi;       /**< @brief Hihi Alarm Limit */
    epicsFloat32        lolo;       /**< @brief Lolo Alarm Limit */
    epicsFloat32        high;       /**< @brief High Alarm Limit */
    epicsFloat32        low;        /**< @brief Low Alarm Limit */
    epicsEnum16         hhsv;       /**< @brief Hihi Severity */
    epicsEnum16         llsv;       /**< @brief Lolo Severity */
    epicsEnum16         hsv;        /**< @brief High Severity */
    epicsEnum16         lsv;        /**< @brief Low Severity */
    epicsFloat64        hyst;       /**< @brief Alarm Deadband */
    epicsFloat64        adel;       /**< @brief Archive Deadband */
    epicsFloat64        mdel;       /**< @brief Monitor Deadband */
    epicsFloat64        lalm;       /**< @brief Last Value Alarmed */
    epicsFloat64        alst;       /**< @brief Last Value Archived */
    epicsFloat64        mlst;       /**< @brief Last Val Monitored */
} xxxRecord;

typedef enum {
	xxxRecordNAME = 0,
	xxxRecordDESC = 1,
	xxxRecordASG = 2,
	xxxRecordSCAN = 3,
	xxxRecordPINI = 4,
	xxxRecordPHAS = 5,
	xxxRecordEVNT = 6,
	xxxRecordTSE = 7,
	xxxRecordTSEL = 8,
	xxxRecordDTYP = 9,
	xxxRecordDISV = 10,
	xxxRecordDISA = 11,
	xxxRecordSDIS = 12,
	xxxRecordMLOK = 13,
	xxxRecordMLIS = 14,
	xxxRecordBKLNK = 15,
	xxxRecordDISP = 16,
	xxxRecordPROC = 17,
	xxxRecordSTAT = 18,
	xxxRecordSEVR = 19,
	xxxRecordAMSG = 20,
	xxxRecordNSTA = 21,
	xxxRecordNSEV = 22,
	xxxRecordNAMSG = 23,
	xxxRecordACKS = 24,
	xxxRecordACKT = 25,
	xxxRecordDISS = 26,
	xxxRecordLCNT = 27,
	xxxRecordPACT = 28,
	xxxRecordPUTF = 29,
	xxxRecordRPRO = 30,
	xxxRecordASP = 31,
	xxxRecordPPN = 32,
	xxxRecordPPNR = 33,
	xxxRecordSPVT = 34,
	xxxRecordRSET = 35,
	xxxRecordDSET = 36,
	xxxRecordDPVT = 37,
	xxxRecordRDES = 38,
	xxxRecordLSET = 39,
	xxxRecordPRIO = 40,
	xxxRecordTPRO = 41,
	xxxRecordBKPT = 42,
	xxxRecordUDF = 43,
	xxxRecordUDFS = 44,
	xxxRecordTIME = 45,
	xxxRecordUTAG = 46,
	xxxRecordFLNK = 47,
	xxxRecordVAL = 48,
	xxxRecordINP = 49,
	xxxRecordPREC = 50,
	xxxRecordEGU = 51,
	xxxRecordHOPR = 52,
	xxxRecordLOPR = 53,
	xxxRecordHIHI = 54,
	xxxRecordLOLO = 55,
	xxxRecordHIGH = 56,
	xxxRecordLOW = 57,
	xxxRecordHHSV = 58,
	xxxRecordLLSV = 59,
	xxxRecordHSV = 60,
	xxxRecordLSV = 61,
	xxxRecordHYST = 62,
	xxxRecordADEL = 63,
	xxxRecordMDEL = 64,
	xxxRecordLALM = 65,
	xxxRecordALST = 66,
	xxxRecordMLST = 67
} xxxFieldIndex;

#ifdef GEN_SIZE_OFFSET

#include <epicsExport.h>
#include <cantProceed.h>
#ifdef __cplusplus
extern "C" {
#endif
static int xxxRecordSizeOffset(dbRecordType *prt)
{
    xxxRecord *prec = 0;

    if (prt->no_fields != 68) {
        cantProceed("IOC build or installation error:\n"
            "    The xxxRecord defined in the DBD file has %d fields,\n"
            "    but the record support code was built with 68.\n",
            prt->no_fields);
    }
    prt->papFldDes[xxxRecordNAME]->size = sizeof(prec->name);
    prt->papFldDes[xxxRecordNAME]->offset = (unsigned short)offsetof(xxxRecord, name);
    prt->papFldDes[xxxRecordDESC]->size = sizeof(prec->desc);
    prt->papFldDes[xxxRecordDESC]->offset = (unsigned short)offsetof(xxxRecord, desc);
    prt->papFldDes[xxxRecordASG]->size = sizeof(prec->asg);
    prt->papFldDes[xxxRecordASG]->offset = (unsigned short)offsetof(xxxRecord, asg);
    prt->papFldDes[xxxRecordSCAN]->size = sizeof(prec->scan);
    prt->papFldDes[xxxRecordSCAN]->offset = (unsigned short)offsetof(xxxRecord, scan);
    prt->papFldDes[xxxRecordPINI]->size = sizeof(prec->pini);
    prt->papFldDes[xxxRecordPINI]->offset = (unsigned short)offsetof(xxxRecord, pini);
    prt->papFldDes[xxxRecordPHAS]->size = sizeof(prec->phas);
    prt->papFldDes[xxxRecordPHAS]->offset = (unsigned short)offsetof(xxxRecord, phas);
    prt->papFldDes[xxxRecordEVNT]->size = sizeof(prec->evnt);
    prt->papFldDes[xxxRecordEVNT]->offset = (unsigned short)offsetof(xxxRecord, evnt);
    prt->papFldDes[xxxRecordTSE]->size = sizeof(prec->tse);
    prt->papFldDes[xxxRecordTSE]->offset = (unsigned short)offsetof(xxxRecord, tse);
    prt->papFldDes[xxxRecordTSEL]->size = sizeof(prec->tsel);
    prt->papFldDes[xxxRecordTSEL]->offset = (unsigned short)offsetof(xxxRecord, tsel);
    prt->papFldDes[xxxRecordDTYP]->size = sizeof(prec->dtyp);
    prt->papFldDes[xxxRecordDTYP]->offset = (unsigned short)offsetof(xxxRecord, dtyp);
    prt->papFldDes[xxxRecordDISV]->size = sizeof(prec->disv);
    prt->papFldDes[xxxRecordDISV]->offset = (unsigned short)offsetof(xxxRecord, disv);
    prt->papFldDes[xxxRecordDISA]->size = sizeof(prec->disa);
    prt->papFldDes[xxxRecordDISA]->offset = (unsigned short)offsetof(xxxRecord, disa);
    prt->papFldDes[xxxRecordSDIS]->size = sizeof(prec->sdis);
    prt->papFldDes[xxxRecordSDIS]->offset = (unsigned short)offsetof(xxxRecord, sdis);
    prt->papFldDes[xxxRecordMLOK]->size = sizeof(prec->mlok);
    prt->papFldDes[xxxRecordMLOK]->offset = (unsigned short)offsetof(xxxRecord, mlok);
    prt->papFldDes[xxxRecordMLIS]->size = sizeof(prec->mlis);
    prt->papFldDes[xxxRecordMLIS]->offset = (unsigned short)offsetof(xxxRecord, mlis);
    prt->papFldDes[xxxRecordBKLNK]->size = sizeof(prec->bklnk);
    prt->papFldDes[xxxRecordBKLNK]->offset = (unsigned short)offsetof(xxxRecord, bklnk);
    prt->papFldDes[xxxRecordDISP]->size = sizeof(prec->disp);
    prt->papFldDes[xxxRecordDISP]->offset = (unsigned short)offsetof(xxxRecord, disp);
    prt->papFldDes[xxxRecordPROC]->size = sizeof(prec->proc);
    prt->papFldDes[xxxRecordPROC]->offset = (unsigned short)offsetof(xxxRecord, proc);
    prt->papFldDes[xxxRecordSTAT]->size = sizeof(prec->stat);
    prt->papFldDes[xxxRecordSTAT]->offset = (unsigned short)offsetof(xxxRecord, stat);
    prt->papFldDes[xxxRecordSEVR]->size = sizeof(prec->sevr);
    prt->papFldDes[xxxRecordSEVR]->offset = (unsigned short)offsetof(xxxRecord, sevr);
    prt->papFldDes[xxxRecordAMSG]->size = sizeof(prec->amsg);
    prt->papFldDes[xxxRecordAMSG]->offset = (unsigned short)offsetof(xxxRecord, amsg);
    prt->papFldDes[xxxRecordNSTA]->size = sizeof(prec->nsta);
    prt->papFldDes[xxxRecordNSTA]->offset = (unsigned short)offsetof(xxxRecord, nsta);
    prt->papFldDes[xxxRecordNSEV]->size = sizeof(prec->nsev);
    prt->papFldDes[xxxRecordNSEV]->offset = (unsigned short)offsetof(xxxRecord, nsev);
    prt->papFldDes[xxxRecordNAMSG]->size = sizeof(prec->namsg);
    prt->papFldDes[xxxRecordNAMSG]->offset = (unsigned short)offsetof(xxxRecord, namsg);
    prt->papFldDes[xxxRecordACKS]->size = sizeof(prec->acks);
    prt->papFldDes[xxxRecordACKS]->offset = (unsigned short)offsetof(xxxRecord, acks);
    prt->papFldDes[xxxRecordACKT]->size = sizeof(prec->ackt);
    prt->papFldDes[xxxRecordACKT]->offset = (unsigned short)offsetof(xxxRecord, ackt);
    prt->papFldDes[xxxRecordDISS]->size = sizeof(prec->diss);
    prt->papFldDes[xxxRecordDISS]->offset = (unsigned short)offsetof(xxxRecord, diss);
    prt->papFldDes[xxxRecordLCNT]->size = sizeof(prec->lcnt);
    prt->papFldDes[xxxRecordLCNT]->offset = (unsigned short)offsetof(xxxRecord, lcnt);
    prt->papFldDes[xxxRecordPACT]->size = sizeof(prec->pact);
    prt->papFldDes[xxxRecordPACT]->offset = (unsigned short)offsetof(xxxRecord, pact);
    prt->papFldDes[xxxRecordPUTF]->size = sizeof(prec->putf);
    prt->papFldDes[xxxRecordPUTF]->offset = (unsigned short)offsetof(xxxRecord, putf);
    prt->papFldDes[xxxRecordRPRO]->size = sizeof(prec->rpro);
    prt->papFldDes[xxxRecordRPRO]->offset = (unsigned short)offsetof(xxxRecord, rpro);
    prt->papFldDes[xxxRecordASP]->size = sizeof(prec->asp);
    prt->papFldDes[xxxRecordASP]->offset = (unsigned short)offsetof(xxxRecord, asp);
    prt->papFldDes[xxxRecordPPN]->size = sizeof(prec->ppn);
    prt->papFldDes[xxxRecordPPN]->offset = (unsigned short)offsetof(xxxRecord, ppn);
    prt->papFldDes[xxxRecordPPNR]->size = sizeof(prec->ppnr);
    prt->papFldDes[xxxRecordPPNR]->offset = (unsigned short)offsetof(xxxRecord, ppnr);
    prt->papFldDes[xxxRecordSPVT]->size = sizeof(prec->spvt);
    prt->papFldDes[xxxRecordSPVT]->offset = (unsigned short)offsetof(xxxRecord, spvt);
    prt->papFldDes[xxxRecordRSET]->size = sizeof(prec->rset);
    prt->papFldDes[xxxRecordRSET]->offset = (unsigned short)offsetof(xxxRecord, rset);
    prt->papFldDes[xxxRecordDSET]->size = sizeof(prec->dset);
    prt->papFldDes[xxxRecordDSET]->offset = (unsigned short)offsetof(xxxRecord, dset);
    prt->papFldDes[xxxRecordDPVT]->size = sizeof(prec->dpvt);
    prt->papFldDes[xxxRecordDPVT]->offset = (unsigned short)offsetof(xxxRecord, dpvt);
    prt->papFldDes[xxxRecordRDES]->size = sizeof(prec->rdes);
    prt->papFldDes[xxxRecordRDES]->offset = (unsigned short)offsetof(xxxRecord, rdes);
    prt->papFldDes[xxxRecordLSET]->size = sizeof(prec->lset);
    prt->papFldDes[xxxRecordLSET]->offset = (unsigned short)offsetof(xxxRecord, lset);
    prt->papFldDes[xxxRecordPRIO]->size = sizeof(prec->prio);
    prt->papFldDes[xxxRecordPRIO]->offset = (unsigned short)offsetof(xxxRecord, prio);
    prt->papFldDes[xxxRecordTPRO]->size = sizeof(prec->tpro);
    prt->papFldDes[xxxRecordTPRO]->offset = (unsigned short)offsetof(xxxRecord, tpro);
    prt->papFldDes[xxxRecordBKPT]->size = sizeof(prec->bkpt);
    prt->papFldDes[xxxRecordBKPT]->offset = (unsigned short)offsetof(xxxRecord, bkpt);
    prt->papFldDes[xxxRecordUDF]->size = sizeof(prec->udf);
    prt->papFldDes[xxxRecordUDF]->offset = (unsigned short)offsetof(xxxRecord, udf);
    prt->papFldDes[xxxRecordUDFS]->size = sizeof(prec->udfs);
    prt->papFldDes[xxxRecordUDFS]->offset = (unsigned short)offsetof(xxxRecord, udfs);
    prt->papFldDes[xxxRecordTIME]->size = sizeof(prec->time);
    prt->papFldDes[xxxRecordTIME]->offset = (unsigned short)offsetof(xxxRecord, time);
    prt->papFldDes[xxxRecordUTAG]->size = sizeof(prec->utag);
    prt->papFldDes[xxxRecordUTAG]->offset = (unsigned short)offsetof(xxxRecord, utag);
    prt->papFldDes[xxxRecordFLNK]->size = sizeof(prec->flnk);
    prt->papFldDes[xxxRecordFLNK]->offset = (unsigned short)offsetof(xxxRecord, flnk);
    prt->papFldDes[xxxRecordVAL]->size = sizeof(prec->val);
    prt->papFldDes[xxxRecordVAL]->offset = (unsigned short)offsetof(xxxRecord, val);
    prt->papFldDes[xxxRecordINP]->size = sizeof(prec->inp);
    prt->papFldDes[xxxRecordINP]->offset = (unsigned short)offsetof(xxxRecord, inp);
    prt->papFldDes[xxxRecordPREC]->size = sizeof(prec->prec);
    prt->papFldDes[xxxRecordPREC]->offset = (unsigned short)offsetof(xxxRecord, prec);
    prt->papFldDes[xxxRecordEGU]->size = sizeof(prec->egu);
    prt->papFldDes[xxxRecordEGU]->offset = (unsigned short)offsetof(xxxRecord, egu);
    prt->papFldDes[xxxRecordHOPR]->size = sizeof(prec->hopr);
    prt->papFldDes[xxxRecordHOPR]->offset = (unsigned short)offsetof(xxxRecord, hopr);
    prt->papFldDes[xxxRecordLOPR]->size = sizeof(prec->lopr);
    prt->papFldDes[xxxRecordLOPR]->offset = (unsigned short)offsetof(xxxRecord, lopr);
    prt->papFldDes[xxxRecordHIHI]->size = sizeof(prec->hihi);
    prt->papFldDes[xxxRecordHIHI]->offset = (unsigned short)offsetof(xxxRecord, hihi);
    prt->papFldDes[xxxRecordLOLO]->size = sizeof(prec->lolo);
    prt->papFldDes[xxxRecordLOLO]->offset = (unsigned short)offsetof(xxxRecord, lolo);
    prt->papFldDes[xxxRecordHIGH]->size = sizeof(prec->high);
    prt->papFldDes[xxxRecordHIGH]->offset = (unsigned short)offsetof(xxxRecord, high);
    prt->papFldDes[xxxRecordLOW]->size = sizeof(prec->low);
    prt->papFldDes[xxxRecordLOW]->offset = (unsigned short)offsetof(xxxRecord, low);
    prt->papFldDes[xxxRecordHHSV]->size = sizeof(prec->hhsv);
    prt->papFldDes[xxxRecordHHSV]->offset = (unsigned short)offsetof(xxxRecord, hhsv);
    prt->papFldDes[xxxRecordLLSV]->size = sizeof(prec->llsv);
    prt->papFldDes[xxxRecordLLSV]->offset = (unsigned short)offsetof(xxxRecord, llsv);
    prt->papFldDes[xxxRecordHSV]->size = sizeof(prec->hsv);
    prt->papFldDes[xxxRecordHSV]->offset = (unsigned short)offsetof(xxxRecord, hsv);
    prt->papFldDes[xxxRecordLSV]->size = sizeof(prec->lsv);
    prt->papFldDes[xxxRecordLSV]->offset = (unsigned short)offsetof(xxxRecord, lsv);
    prt->papFldDes[xxxRecordHYST]->size = sizeof(prec->hyst);
    prt->papFldDes[xxxRecordHYST]->offset = (unsigned short)offsetof(xxxRecord, hyst);
    prt->papFldDes[xxxRecordADEL]->size = sizeof(prec->adel);
    prt->papFldDes[xxxRecordADEL]->offset = (unsigned short)offsetof(xxxRecord, adel);
    prt->papFldDes[xxxRecordMDEL]->size = sizeof(prec->mdel);
    prt->papFldDes[xxxRecordMDEL]->offset = (unsigned short)offsetof(xxxRecord, mdel);
    prt->papFldDes[xxxRecordLALM]->size = sizeof(prec->lalm);
    prt->papFldDes[xxxRecordLALM]->offset = (unsigned short)offsetof(xxxRecord, lalm);
    prt->papFldDes[xxxRecordALST]->size = sizeof(prec->alst);
    prt->papFldDes[xxxRecordALST]->offset = (unsigned short)offsetof(xxxRecord, alst);
    prt->papFldDes[xxxRecordMLST]->size = sizeof(prec->mlst);
    prt->papFldDes[xxxRecordMLST]->offset = (unsigned short)offsetof(xxxRecord, mlst);
    prt->rec_size = sizeof(*prec);
    return 0;
}
epicsExportRegistrar(xxxRecordSizeOffset);

#ifdef __cplusplus
}
#endif
#endif /* GEN_SIZE_OFFSET */

#endif /* INC_xxxRecord_H */
