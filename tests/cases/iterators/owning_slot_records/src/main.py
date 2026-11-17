# Owning element slots fed by user records with a consuming `__iter__` and by
# comprehensions that consume or lend their source; one part per topic.
import part_records
import part_consuming
import part_record_frames
import part_record_consumers
import part_dispatch_order
import part_comp_consuming
import part_comp_lent
import part_own_param


def main() -> None:
    part_records.main()
    part_consuming.main()
    part_record_frames.main()
    part_record_consumers.main()
    part_dispatch_order.main()
    part_comp_consuming.main()
    part_comp_lent.main()
    part_own_param.main()


main()
