package org.broadinstitute.hail.driver

import org.apache.spark.sql.Row
import org.broadinstitute.hail.annotations._
import org.broadinstitute.hail.expr
import org.broadinstitute.hail.variant._
import org.kohsuke.args4j.{Option => Args4jOption}
import org.broadinstitute.hail.Utils._
import scala.collection.mutable

object SplitMulti extends Command {
  def name = "splitmulti"

  def description = "Split multi-allelic sites in the current dataset"

  override def supportsMultiallelic = true

  class Options extends BaseOptions {
    @Args4jOption(required = false, name = "--propagate-gq", usage = "Propagate GQ instead of computing from PL")
    var propagateGQ: Boolean = false
  }

  def newOptions = new Options

  def splitGT(gt: Int, i: Int): Int = {
    val p = Genotype.gtPair(gt)
    (if (p.j == i) 1 else 0) +
      (if (p.k == i) 1 else 0)
  }

  def minRep(start: Int, ref: String, alt: String): (Int, String, String) = {
    require(ref != alt)
    var newStart = start

    var refe = ref.length
    var alte = alt.length
    while (refe > 1
      && alte > 1
      && ref(refe - 1) == alt(alte - 1)) {
      refe -= 1
      alte -= 1
    }

    var refs = 0
    var alts = 0
    while (ref(refs) == alt(alts)
      && refs + 1 < refe
      && alts + 1 < alte) {
      newStart += 1
      refs += 1
      alts += 1
    }

    assert(refs < refe && alts < alte)
    (newStart, ref.substring(refs, refe), alt.substring(alts, alte))
  }

  def split(v: Variant,
    va: Annotation,
    it: Iterable[Genotype],
    propagateGQ: Boolean,
    insertIndex: Inserter,
    insertSplit: Inserter): Iterator[(Variant, Annotation, Iterable[Genotype])] = {
    if (v.isBiallelic)
      return Iterator((v, insertSplit(insertIndex(va, Some(0)), Some(false)), it))

    val splitVariants = v.altAlleles.iterator.zipWithIndex
      .filter(_._1.alt != "*")
      .map { case (aa, i) =>
        val (newStart, newRef, newAlt) = minRep(v.start, v.ref, aa.alt)

        (Variant(v.contig, newStart, newRef, newAlt), i + 1)
      }.toArray

    val splitGenotypeBuilders = splitVariants.map { case (sv, _) => new GenotypeBuilder(sv) }
    val splitGenotypeStreamBuilders = splitVariants.map { case (sv, _) => new GenotypeStreamBuilder(sv, true) }

    for (g <- it) {

      val gadsum = g.ad.map(gadx => (gadx, gadx.sum))

      // svj corresponds to the ith allele of v
      for (((svj, i), j) <- splitVariants.iterator.zipWithIndex) {
        val gb = splitGenotypeBuilders(j)

        gb.clear()
        g.gt.foreach { ggtx =>
          val gtx = splitGT(ggtx, i)
          gb.setGT(gtx)

          val p = Genotype.gtPair(ggtx)
          if (gtx != p.nNonRefAlleles)
            gb.setFakeRef()
        }

        gadsum.foreach { case (gadx, sum) =>
          // what bcftools does
          // Array(gadx(0), gadx(i))
          gb.setAD(Array(sum - gadx(i), gadx(i)))
        }

        g.dp.foreach { dpx => gb.setDP(dpx) }

        if (propagateGQ)
          g.gq.foreach { gqx => gb.setGQ(gqx) }

        g.pl.foreach { gplx =>
          val plx = gplx.iterator.zipWithIndex
            .map { case (p, k) => (splitGT(k, i), p) }
            .reduceByKeyToArray(3, Int.MaxValue)(_ min _)
          gb.setPL(plx)

          if (!propagateGQ) {
            val gq = Genotype.gqFromPL(plx)
            gb.setGQ(gq)
          }
        }

        splitGenotypeStreamBuilders(j).write(gb)
      }
    }

    splitVariants.iterator
      .zip(splitGenotypeStreamBuilders.iterator)
      .map { case ((v, ind), gsb) =>
        (v, insertSplit(insertIndex(va, Some(ind)), Some(true)), gsb.result())
      }
  }

  def run(state: State, options: Options): State = {
    val localPropagateGQ = options.propagateGQ
    val (newSigs1, insertIndex) = state.vds.metadata.vaSignatures.insert(List("aIndex"),
      SimpleSignature(expr.TInt))
    val (newSigs2, insertSplit) = newSigs1.insert(List("wasSplit"),
      SimpleSignature(expr.TBoolean))
    val newVDS = state.vds.copy[Genotype](
      wasSplit = true,
      vaSignatures = newSigs2,
      rdd = state.vds.rdd.flatMap[(Variant, Annotation, Iterable[Genotype])] { case (v, va, it) =>
        split(v, va, it, localPropagateGQ, insertIndex, insertSplit)
      })
    state.copy(vds = newVDS)
  }
}
